"""教师端路由：建班、试卷管理、出题（手动/AI）、班级管理。

教师「一套一套地」出题：班级 → ProblemSet（如「第2节课」） → Question。
"""
import csv
import io
import random
import string
import urllib.parse
from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response, Form, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import templates, require_role
from ..models import (User, Class, ProblemSet, Question, Choice, Enrollment, Answer,
                      QType, QuestionSource, Role, class_leaderboard)

router = APIRouter(prefix="/teacher", tags=["teacher"])
teacher_dep = require_role(Role.teacher)


def _csv_response(filename: str, rows: list[list[str]]) -> Response:
    """构造 UTF-8 BOM 的 CSV 下载响应（Excel 直接双击打开不乱码）。"""
    buf = io.StringIO()
    buf.write("\ufeff")  # UTF-8 BOM
    writer = csv.writer(buf)
    for r in rows:
        writer.writerow(r)
    # 文件名同时给 ASCII fallback + UTF-8 编码版本，兼容各浏览器
    ascii_name = filename.encode("ascii", "ignore").decode("ascii") or "export.csv"
    quoted = urllib.parse.quote(filename)
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": disposition},
    )


def _gen_join_code(db: Session) -> str:
    """生成 6 位大写字母+数字 join_code，DB 查重，最多重试 5 次。"""
    chars = string.ascii_uppercase + string.digits
    for _ in range(5):
        code = "".join(random.choices(chars, k=6))
        if not db.query(Class).filter(Class.join_code == code).first():
            return code
    raise RuntimeError("生成 join_code 失败：5 次碰撞")


def _get_owned_class(db: Session, cid: int, user: User) -> Class:
    cls = db.query(Class).filter(Class.id == cid, Class.teacher_id == user.id).first()
    if not cls:
        raise HTTPException(404, "班级不存在")
    return cls


def _get_owned_set(db: Session, cid: int, sid: int, user: User) -> ProblemSet:
    """取该班级下的试卷；班级不存在则 404，试卷不存在则 404。"""
    _get_owned_class(db, cid, user)  # 校验班级归属
    s = db.query(ProblemSet).filter(ProblemSet.id == sid,
                                    ProblemSet.class_id == cid).first()
    if not s:
        raise HTTPException(404, "试卷不存在")
    return s


@router.get("")
async def dashboard(request: Request, user: User = Depends(teacher_dep),
                    db: Session = Depends(get_db)):
    classes = db.query(Class).filter(Class.teacher_id == user.id).order_by(Class.created_at.desc()).all()
    stats = []
    for c in classes:
        n_students = db.query(Enrollment).filter(Enrollment.class_id == c.id).count()
        n_questions = db.query(Question).filter(Question.class_id == c.id).count()
        n_sets = db.query(ProblemSet).filter(ProblemSet.class_id == c.id).count()
        stats.append({"class": c, "n_students": n_students,
                      "n_questions": n_questions, "n_sets": n_sets})
    return templates.TemplateResponse(request, "teacher/dashboard.html", {
        "user": user, "stats": stats})


@router.get("/classes/new")
async def new_class_page(request: Request, user: User = Depends(teacher_dep)):
    return templates.TemplateResponse(request, "teacher/new_class.html",
                                     {"user": user, "error": None})


@router.post("/classes/new")
async def create_class(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    user: User = Depends(teacher_dep),
    db: Session = Depends(get_db),
):
    name = name.strip()
    if len(name) < 2:
        return templates.TemplateResponse(request, "teacher/new_class.html",
            {"user": user, "error": "班级名称至少 2 个字"}, status_code=400)
    code = _gen_join_code(db)
    cls = Class(name=name, description=description.strip(), join_code=code, teacher_id=user.id)
    db.add(cls)
    db.commit()
    db.refresh(cls)
    return Response(status_code=303, headers={"Location": f"/teacher/classes/{cls.id}"})


@router.get("/classes/{cid}")
async def class_detail(cid: int, request: Request,
                       user: User = Depends(teacher_dep),
                       db: Session = Depends(get_db)):
    cls = _get_owned_class(db, cid, user)
    sets = (db.query(ProblemSet)
            .filter(ProblemSet.class_id == cid)
            .order_by(ProblemSet.created_at).all())
    set_stats = []
    for s in sets:
        n_q = db.query(Question).filter(Question.set_id == s.id).count()
        set_stats.append({"set": s, "n_questions": n_q})
    students = (db.query(User)
                .join(Enrollment, Enrollment.student_id == User.id)
                .filter(Enrollment.class_id == cid)
                .all())
    return templates.TemplateResponse(request, "teacher/class_detail.html", {
        "user": user, "cls": cls,
        "set_stats": set_stats, "students": students})


# —— 试卷 CRUD ——

@router.post("/classes/{cid}/sets")
async def create_set(cid: int, request: Request,
                     name: str = Form(...),
                     description: str = Form(""),
                     user: User = Depends(teacher_dep),
                     db: Session = Depends(get_db)):
    cls = _get_owned_class(db, cid, user)
    name = name.strip()
    if len(name) < 1:
        raise HTTPException(400, "试卷名称不能为空")
    s = ProblemSet(class_id=cls.id, name=name, description=description.strip())
    db.add(s)
    db.commit()
    db.refresh(s)
    return Response(status_code=303, headers={"Location": f"/teacher/classes/{cid}/sets/{s.id}"})


@router.get("/classes/{cid}/sets/{sid}")
async def set_detail(cid: int, sid: int, request: Request,
                     user: User = Depends(teacher_dep),
                     db: Session = Depends(get_db)):
    cls = _get_owned_class(db, cid, user)
    s = _get_owned_set(db, cid, sid, user)
    questions = (db.query(Question)
                 .filter(Question.set_id == sid)
                 .order_by(Question.created_at).all())
    return templates.TemplateResponse(request, "teacher/set_detail.html", {
        "user": user, "cls": cls, "set": s, "questions": questions})


@router.post("/classes/{cid}/sets/{sid}/delete")
async def delete_set(cid: int, sid: int,
                     user: User = Depends(teacher_dep),
                     db: Session = Depends(get_db)):
    cls = _get_owned_class(db, cid, user)
    s = _get_owned_set(db, cid, sid, user)
    db.delete(s)  # cascade 删除其下题目、选项、作答
    db.commit()
    return Response(status_code=303, headers={"Location": f"/teacher/classes/{cid}"})


# —— 出题（必挂在某个试卷下） ——

@router.get("/classes/{cid}/sets/{sid}/questions/new")
async def new_question_page(cid: int, sid: int, request: Request,
                            user: User = Depends(teacher_dep),
                            db: Session = Depends(get_db)):
    cls = _get_owned_class(db, cid, user)
    s = _get_owned_set(db, cid, sid, user)
    return templates.TemplateResponse(request, "teacher/new_question.html", {
        "user": user, "cls": cls, "set": s,
        "error": None, "ai_message": None})


@router.post("/classes/{cid}/sets/{sid}/questions")
async def create_question(
    cid: int, sid: int, request: Request,
    q_type: str = Form(...),
    stem: str = Form(...),
    explanation: str = Form(""),
    # 选择题选项（manual 模式）
    choice_texts: list[str] = Form(default=[]),
    choice_correct: list[str] = Form(default=[]),  # 选择题：勾选的正确项索引
    # 判断题正确答案（独立字段，避免与 choice_correct 冲突）
    tf_correct: str = Form(default="false"),
    user: User = Depends(teacher_dep),
    db: Session = Depends(get_db),
):
    cls = _get_owned_class(db, cid, user)
    s = _get_owned_set(db, cid, sid, user)

    q = Question(class_id=cid, set_id=sid, q_type=QType(q_type), stem=stem.strip(),
                 explanation=explanation.strip(), source=QuestionSource.manual)
    db.add(q)
    db.flush()

    if q.q_type == QType.tf:
        # 判断题：tf_correct 存的是 "true"/"false"
        db.add(Choice(question_id=q.id, text="对", is_correct=(tf_correct == "true")))
        db.add(Choice(question_id=q.id, text="错", is_correct=(tf_correct != "true")))
    else:
        # 选择题
        if len(choice_texts) < 2:
            db.rollback()
            return templates.TemplateResponse(request, "teacher/new_question.html", {
                "user": user, "cls": cls, "set": s,
                "error": "选择题至少 2 个选项", "ai_message": None}, status_code=400)
        correct_idx = [int(i) for i in choice_correct] if choice_correct else []
        if len(correct_idx) != 1:
            db.rollback()
            return templates.TemplateResponse(request, "teacher/new_question.html", {
                "user": user, "cls": cls, "set": s,
                "error": "必须勾选恰好 1 个正确选项", "ai_message": None}, status_code=400)
        for i, txt in enumerate(choice_texts):
            txt = txt.strip()
            if not txt:
                continue
            db.add(Choice(question_id=q.id, text=txt, is_correct=(i in correct_idx)))

    db.commit()
    return Response(status_code=303,
                    headers={"Location": f"/teacher/classes/{cid}/sets/{sid}/questions/new"})


@router.post("/classes/{cid}/sets/{sid}/questions/ai-generate")
async def ai_generate(cid: int, sid: int, request: Request,
                      ai_type: str = Form("mc"),
                      user: User = Depends(teacher_dep),
                      db: Session = Depends(get_db)):
    """AI 出题：在指定试卷下直接落库一道题，回到出题页显示结果。"""
    from ..ai import generate_mc_question, generate_tf_question
    cls = _get_owned_class(db, cid, user)
    s = _get_owned_set(db, cid, sid, user)
    ai_message = None
    error = None
    try:
        if ai_type == "tf":
            data = generate_tf_question()
            q = Question(class_id=cid, set_id=sid, q_type=QType.tf, stem=data["stem"],
                        explanation=data["explanation"], source=QuestionSource.ai)
            db.add(q); db.flush()
            db.add(Choice(question_id=q.id, text="对", is_correct=bool(data["is_correct"])))
            db.add(Choice(question_id=q.id, text="错", is_correct=not bool(data["is_correct"])))
            db.commit()
            ai_message = "✅ AI 已生成一道判断题并入库！"
        else:
            out = generate_mc_question()
            q = Question(class_id=cid, set_id=sid, q_type=QType.mc, stem=out.stem,
                        explanation=out.explanation, source=QuestionSource.ai)
            db.add(q); db.flush()
            for c in out.choices:
                db.add(Choice(question_id=q.id, text=c.text, is_correct=c.is_correct))
            db.commit()
            ai_message = "✅ AI 已生成一道选择题并入库！"
    except Exception as e:
        error = f"AI 出题失败：{e}。请检查 DEEPSEEK_API_KEY 配置，或改用手动录入。"
    return templates.TemplateResponse(request, "teacher/new_question.html", {
        "user": user, "cls": cls, "set": s,
        "error": error, "ai_message": ai_message})


@router.post("/classes/{cid}/questions/{qid}/delete")
async def delete_question(cid: int, qid: int,
                          user: User = Depends(teacher_dep),
                          db: Session = Depends(get_db)):
    cls = _get_owned_class(db, cid, user)
    q = db.query(Question).filter(Question.id == qid, Question.class_id == cid).first()
    sid = q.set_id if q else None
    if q:
        db.delete(q)
        db.commit()
    target = f"/teacher/classes/{cid}/sets/{sid}" if sid else f"/teacher/classes/{cid}"
    return Response(status_code=303, headers={"Location": target})


# —— 学生管理：移除学生 + 其在本班的所有答题记录 ——
@router.get("/classes/{cid}/students")
async def class_students(cid: int, request: Request,
                         user: User = Depends(teacher_dep),
                         db: Session = Depends(get_db)):
    cls = _get_owned_class(db, cid, user)
    ranking = class_leaderboard(db, cid)
    # 把 (rank, score, answered) 注入到学生对象
    rank_map = {r["user_id"]: r for r in ranking}
    students = (db.query(User)
                .join(Enrollment, Enrollment.student_id == User.id)
                .filter(Enrollment.class_id == cid)
                .order_by(User.id).all())
    rows = []
    for s in students:
        info = rank_map.get(s.id, {"rank": None, "score": 0, "answered": 0})
        rows.append({"user": s, "rank": info["rank"],
                     "score": info["score"], "answered": info["answered"]})
    return templates.TemplateResponse(request, "teacher/class_students.html", {
        "user": user, "cls": cls, "rows": rows})


@router.post("/classes/{cid}/students/{uid}/remove")
async def remove_student(cid: int, uid: int,
                         user: User = Depends(teacher_dep),
                         db: Session = Depends(get_db)):
    _get_owned_class(db, cid, user)  # 校验班级归属
    # 1) 删除该学生在本班级所有题目的答题记录
    #    用子查询拿本班所有 question_id，再 IN 删除（避免 bulk delete 不支持 join）
    qids_subq = db.query(Question.id).filter(Question.class_id == cid).subquery()
    db.query(Answer).filter(
        Answer.student_id == uid,
        Answer.question_id.in_(select(qids_subq))
    ).delete(synchronize_session=False)
    # 2) 删除该学生在本班级的选课记录
    db.query(Enrollment).filter(
        Enrollment.class_id == cid, Enrollment.student_id == uid
    ).delete(synchronize_session=False)
    db.commit()
    return Response(status_code=303, headers={"Location": f"/teacher/classes/{cid}/students"})


# —— 导出全班学生汇总 CSV ——
# ⚠️ 此路由必须放在 /students/{uid} 之前，否则 "export" 会被当作 uid 匹配
@router.get("/classes/{cid}/students/export")
async def export_class_students(cid: int,
                                user: User = Depends(teacher_dep),
                                db: Session = Depends(get_db)):
    cls = _get_owned_class(db, cid, user)
    ranking = class_leaderboard(db, cid)
    rank_map = {r["user_id"]: r for r in ranking}
    students = (db.query(User)
                .join(Enrollment, Enrollment.student_id == User.id)
                .filter(Enrollment.class_id == cid)
                .order_by(User.id).all())
    rows = [["排名", "学号/工号", "昵称", "已答题数", "总分"]]
    for s in students:
        info = rank_map.get(s.id, {"rank": "—", "score": 0, "answered": 0})
        rows.append([info["rank"], s.email, s.username,
                     info["answered"], info["score"]])
    fname = f"{cls.name}_学生汇总_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return _csv_response(fname, rows)


# —— 学生详情：查看单生在本班的所有答题记录 ——
@router.get("/classes/{cid}/students/{uid}")
async def student_detail(cid: int, uid: int, request: Request,
                         user: User = Depends(teacher_dep),
                         db: Session = Depends(get_db)):
    cls = _get_owned_class(db, cid, user)
    stu = (db.query(User)
           .join(Enrollment, Enrollment.student_id == User.id)
           .filter(Enrollment.class_id == cid, User.id == uid,
                   User.role == Role.student).first())
    if not stu:
        raise HTTPException(404, "该学生不在此班级")
    # 取该学生在本班所有答题记录，按题目创建顺序
    rows = (db.query(Answer, Question, Choice)
            .join(Question, Answer.question_id == Question.id)
            .outerjoin(Choice, Answer.choice_id == Choice.id)
            .filter(Question.class_id == cid, Answer.student_id == uid)
            .order_by(Question.created_at, Answer.answered_at).all())
    # 组装展示数据
    answer_rows = []
    for a, q, picked in rows:
        # 该题的正确选项（可能多个，判断题只有 1 个）
        correct_choices = [c.text for c in q.choices if c.is_correct]
        answer_rows.append({
            "q": q,
            "answer": a,
            "picked_text": picked.text if picked else "（未选）",
            "correct_text": " / ".join(correct_choices),
        })
    # 统计
    n_answered = len(answer_rows)
    n_correct = sum(1 for r in answer_rows if r["answer"].is_correct)
    total_pts = sum(r["answer"].points_awarded for r in answer_rows)
    max_pts = sum(r["q"].points for r in answer_rows) if answer_rows else 0
    return templates.TemplateResponse(request, "teacher/student_detail.html", {
        "user": user, "cls": cls, "stu": stu,
        "answer_rows": answer_rows,
        "n_answered": n_answered, "n_correct": n_correct,
        "total_pts": total_pts, "max_pts": max_pts,
    })


# —— 导出单生答题记录 CSV ——
@router.get("/classes/{cid}/students/{uid}/export")
async def export_student_answers(cid: int, uid: int,
                                 user: User = Depends(teacher_dep),
                                 db: Session = Depends(get_db)):
    cls = _get_owned_class(db, cid, user)
    stu = (db.query(User)
           .join(Enrollment, Enrollment.student_id == User.id)
           .filter(Enrollment.class_id == cid, User.id == uid).first())
    if not stu:
        raise HTTPException(404, "该学生不在此班级")
    rows_data = (db.query(Answer, Question, Choice)
                 .join(Question, Answer.question_id == Question.id)
                 .outerjoin(Choice, Answer.choice_id == Choice.id)
                 .filter(Question.class_id == cid, Answer.student_id == uid)
                 .order_by(Question.created_at, Answer.answered_at).all())
    rows = [["题号", "题型", "分值", "题目", "学生选择", "正确答案",
             "是否正确", "得分", "答题时间"]]
    for i, (a, q, picked) in enumerate(rows_data, 1):
        correct_choices = " / ".join(c.text for c in q.choices if c.is_correct)
        rows.append([
            i,
            "选择题" if q.q_type == QType.mc else "判断题",
            q.points,
            q.stem,
            picked.text if picked else "（未选）",
            correct_choices,
            "✓ 正确" if a.is_correct else "✗ 错误",
            a.points_awarded,
            a.answered_at.strftime("%Y-%m-%d %H:%M:%S") if a.answered_at else "",
        ])
    fname = f"{cls.name}_{stu.email}_{stu.username}_答题记录_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return _csv_response(fname, rows)


# —— 题库：教师历史所有题目，可复制到当前试卷 ——
@router.get("/classes/{cid}/sets/{sid}/bank")
async def question_bank(cid: int, sid: int, request: Request,
                        user: User = Depends(teacher_dep),
                        db: Session = Depends(get_db)):
    cls = _get_owned_class(db, cid, user)
    s = _get_owned_set(db, cid, sid, user)
    # 当前试卷已有题目 id
    existing_ids = {q.id for q in
                    db.query(Question).filter(Question.set_id == sid).all()}
    # 题库：该教师所有班级下的所有题目，按创建时间倒序
    bank = (db.query(Question)
            .join(Class, Question.class_id == Class.id)
            .filter(Class.teacher_id == user.id)
            .order_by(Question.created_at.desc()).all())
    rows = []
    for q in bank:
        if q.id in existing_ids:
            continue  # 已经在本试卷了
        rows.append({
            "q": q,
            "from_class": q.class_,
            "from_set": q.set_,
            "n_choices": len(q.choices),
        })
    return templates.TemplateResponse(request, "teacher/question_bank.html", {
        "user": user, "cls": cls, "set": s, "rows": rows})


@router.post("/classes/{cid}/sets/{sid}/bank/{qid}/add")
async def add_from_bank(cid: int, sid: int, qid: int,
                        user: User = Depends(teacher_dep),
                        db: Session = Depends(get_db)):
    _get_owned_class(db, cid, user)  # 校验班级归属
    _get_owned_set(db, cid, sid, user)  # 校验试卷归属
    src = (db.query(Question)
           .join(Class, Question.class_id == Class.id)
           .filter(Class.teacher_id == user.id, Question.id == qid).first())
    if not src:
        raise HTTPException(404, "题库中找不到该题")
    # 复制 Question + Choices 到当前试卷（学生作答记录不动，仍归原题）
    new_q = Question(
        class_id=cid, set_id=sid,
        q_type=src.q_type, stem=src.stem, explanation=src.explanation,
        source=src.source, points=src.points)
    db.add(new_q)
    db.flush()
    for c in src.choices:
        db.add(Choice(question_id=new_q.id, text=c.text, is_correct=c.is_correct))
    db.commit()
    return Response(status_code=303,
                    headers={"Location": f"/teacher/classes/{cid}/sets/{sid}"})
