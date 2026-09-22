"""学生端路由：加入班级、按试卷顺序答题（即时反馈）、查分。

答题流：
  班级页 → 显示该班级的试卷列表（不显示题目列表）
  点「开始/继续」某套试卷 → 跳到该试卷第一道未答题
  答完一题 → answer_result 页自动 3 秒后跳到下一题
  试卷全部答完 → 自动跳转该班级排行榜（含烟花/奖杯动画）
"""
from fastapi import APIRouter, Depends, Request, Response, Form, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import templates, require_role
from ..models import (User, Class, Enrollment, ProblemSet, Question, Choice, Answer,
                      Role)
from ..achievements import evaluate_achievements

router = APIRouter(prefix="/student", tags=["student"])
student_dep = require_role(Role.student)


def _ensure_enrolled(db: Session, cid: int, user: User) -> Class:
    en = db.query(Enrollment).filter(
        Enrollment.student_id == user.id, Enrollment.class_id == cid).first()
    if not en:
        raise HTTPException(403, "你未加入该班级")
    cls = db.query(Class).filter(Class.id == cid).first()
    if not cls:
        raise HTTPException(404, "班级不存在")
    return cls


def _set_progress(db: Session, sid: int, user: User):
    """返回 (set, questions, answered_ids, score_in_set, n_total, n_done)。"""
    s = db.query(ProblemSet).filter(ProblemSet.id == sid).first()
    if not s:
        raise HTTPException(404, "试卷不存在")
    questions = (db.query(Question)
                 .filter(Question.set_id == sid)
                 .order_by(Question.created_at).all())
    qids = [q.id for q in questions]
    answers = (db.query(Answer)
               .filter(Answer.student_id == user.id,
                       Answer.question_id.in_(qids)).all()) if qids else []
    answered_ids = {a.question_id for a in answers}
    score = sum(a.points_awarded for a in answers)
    return s, questions, answered_ids, score, len(questions), len(answered_ids)


@router.get("")
async def dashboard(request: Request, user: User = Depends(student_dep),
                    db: Session = Depends(get_db)):
    enrollments = (db.query(Enrollment, Class)
                   .join(Class, Class.id == Enrollment.class_id)
                   .filter(Enrollment.student_id == user.id)
                   .order_by(Enrollment.joined_at.desc()).all())
    classes_info = []
    for _, cls in enrollments:
        # 该班级下所有题（跨试卷汇总）
        total_q = db.query(Question).filter(Question.class_id == cls.id).count()
        answered = (db.query(Answer).join(Question, Question.id == Answer.question_id)
                    .filter(Answer.student_id == user.id, Question.class_id == cls.id).count())
        correct = (db.query(Answer).join(Question, Question.id == Answer.question_id)
                   .filter(Answer.student_id == user.id, Question.class_id == cls.id,
                           Answer.is_correct.is_(True)).count())
        score = sum(a.points_awarded for a in
                    (db.query(Answer).join(Question, Question.id == Answer.question_id)
                     .filter(Answer.student_id == user.id, Question.class_id == cls.id).all()))
        n_sets = db.query(ProblemSet).filter(ProblemSet.class_id == cls.id).count()
        classes_info.append({"cls": cls, "total_q": total_q, "answered": answered,
                             "correct": correct, "score": score, "n_sets": n_sets})
    return templates.TemplateResponse(request, "student/dashboard.html", {
        "user": user, "classes_info": classes_info})


@router.get("/join")
async def join_page(request: Request, user: User = Depends(student_dep)):
    return templates.TemplateResponse(request, "student/join.html",
                                     {"user": user, "error": None})


@router.post("/join")
async def join_class(request: Request, code: str = Form(...),
                     user: User = Depends(student_dep),
                     db: Session = Depends(get_db)):
    code = code.strip().upper()
    cls = db.query(Class).filter(Class.join_code == code).first()
    if not cls:
        return templates.TemplateResponse(request, "student/join.html",
            {"user": user, "error": "班级码无效"}, status_code=400)
    existing = db.query(Enrollment).filter(
        Enrollment.student_id == user.id, Enrollment.class_id == cls.id).first()
    if existing:
        return Response(status_code=303, headers={"Location": f"/student/classes/{cls.id}"})
    db.add(Enrollment(student_id=user.id, class_id=cls.id))
    db.commit()
    return Response(status_code=303, headers={"Location": f"/student/classes/{cls.id}"})


@router.get("/classes/{cid}")
async def class_view(cid: int, request: Request,
                     user: User = Depends(student_dep),
                     db: Session = Depends(get_db)):
    cls = _ensure_enrolled(db, cid, user)
    # 班级主页 = 闯关路径（顺序解锁）
    from .levels import level_path_view
    path = level_path_view(db, cid, user)
    return templates.TemplateResponse(request, "student/level_path.html", {
        "user": user, "cls": cls,
        "items": path["items"], "current_idx": path["current_idx"],
        "badge_tiers": path["badge_tiers"],
        "n_badges": path["n_badges"], "n_badges_total": path["n_badges_total"],
        "fresh_badges": path["fresh_badges"], "next_hint": path["next_hint"],
        "celebrate": request.query_params.get("celebrate") == "1"})


@router.get("/classes/{cid}/sets/{sid}/start")
async def start_set(cid: int, sid: int,
                    user: User = Depends(student_dep),
                    db: Session = Depends(get_db)):
    """进入试卷：跳到该试卷第一道未答题；若全部答完则跳到排行榜。"""
    _ensure_enrolled(db, cid, user)
    _s, questions, answered_ids, _, _, _ = _set_progress(db, sid, user)
    if not questions:
        # 试卷还没题，回班级页
        return Response(status_code=303,
                        headers={"Location": f"/student/classes/{cid}"})
    next_q = next((q for q in questions if q.id not in answered_ids), None)
    if next_q is None:
        # 全部答完 → 去试卷解析页看作答与解析
        return Response(status_code=303,
                        headers={"Location": f"/student/classes/{cid}/sets/{sid}/review"})
    return Response(status_code=303,
                    headers={"Location": f"/student/classes/{cid}/questions/{next_q.id}"})


def _next_or_leaderboard(cid: int, q: Question, user: User, db: Session) -> Response:
    """提交后跳转：还有未答题→下一题；都答完→排行榜(带 ?set= 上下文以显示总分横幅)。"""
    if q.set_id:
        questions = (db.query(Question)
                     .filter(Question.set_id == q.set_id)
                     .order_by(Question.created_at).all())
        qids = [qq.id for qq in questions]
        answered_ids = {a.question_id for a in
                        db.query(Answer).filter(
                            Answer.student_id == user.id,
                            Answer.question_id.in_(qids)).all()} if qids else set()
        next_q = next((qq for qq in questions if qq.id not in answered_ids), None)
        if next_q:
            return Response(status_code=303,
                            headers={"Location": f"/student/classes/{cid}/questions/{next_q.id}"})
        # 该试卷全部答完 → 排行榜（带 ?set= 让排行榜显示「这套题得分」横幅）
        return Response(status_code=303,
                        headers={"Location": f"/classes/{cid}/leaderboard?set={q.set_id}"})
    return Response(status_code=303,
                    headers={"Location": f"/student/classes/{cid}"})


@router.get("/classes/{cid}/questions/{qid}")
async def question_page(cid: int, qid: int, request: Request,
                        user: User = Depends(student_dep),
                        db: Session = Depends(get_db)):
    cls = _ensure_enrolled(db, cid, user)
    q = db.query(Question).filter(Question.id == qid, Question.class_id == cid).first()
    if not q:
        raise HTTPException(404, "题目不存在")
    existing = db.query(Answer).filter(
        Answer.student_id == user.id, Answer.question_id == qid).first()
    # 已答过：防作弊，不显示题目选项，直接跳到下一题或排行榜
    if existing:
        return _next_or_leaderboard(cid, q, user, db)
    # 计算进度：第 X/Y 题
    if q.set_id:
        questions = (db.query(Question)
                     .filter(Question.set_id == q.set_id)
                     .order_by(Question.created_at).all())
        idx = next((i + 1 for i, qq in enumerate(questions) if qq.id == qid), 0)
        n_total = len(questions)
    else:
        idx, n_total = 0, 0
    return templates.TemplateResponse(request, "student/question.html", {
        "user": user, "cls": cls, "q": q,
        "q_index": idx, "q_total": n_total})


@router.post("/classes/{cid}/questions/{qid}/answer")
async def submit_answer(cid: int, qid: int,
                        choice_id: int = Form(...),
                        user: User = Depends(student_dep),
                        db: Session = Depends(get_db)):
    """提交答案后直接跳下一题；最后一题跳排行榜（无中间结果页）。"""
    _ensure_enrolled(db, cid, user)
    q = db.query(Question).filter(Question.id == qid, Question.class_id == cid).first()
    if not q:
        raise HTTPException(404, "题目不存在")
    existing = db.query(Answer).filter(
        Answer.student_id == user.id, Answer.question_id == qid).first()
    if existing:
        # 重复提交：直接走「下一题」逻辑
        return _next_or_leaderboard(cid, q, user, db)
    ch = db.query(Choice).filter(Choice.id == choice_id, Choice.question_id == qid).first()
    if not ch:
        raise HTTPException(400, "选项无效")
    is_correct = ch.is_correct
    pts = q.points if is_correct else 0
    ans = Answer(student_id=user.id, question_id=qid, choice_id=choice_id,
                 is_correct=is_correct, points_awarded=pts)
    db.add(ans)
    db.commit()
    db.refresh(ans)
    evaluate_achievements(db, user)
    return _next_or_leaderboard(cid, q, user, db)


@router.get("/classes/{cid}/sets/{sid}/review")
async def set_review(cid: int, sid: int, request: Request,
                     user: User = Depends(student_dep),
                     db: Session = Depends(get_db)):
    """试卷解析页：逐题展示题干/选项/学生作答/正确答案/解析。"""
    cls = _ensure_enrolled(db, cid, user)
    s, questions, _answered_ids, score, n_total, n_done = _set_progress(db, sid, user)
    review_items = []
    for q in questions:
        ans = db.query(Answer).filter(
            Answer.student_id == user.id, Answer.question_id == q.id).first()
        correct_choice = db.query(Choice).filter(
            Choice.question_id == q.id, Choice.is_correct.is_(True)).first()
        review_items.append({
            "q": q, "ans": ans, "correct_choice": correct_choice,
            "answered": ans is not None,
        })
    return templates.TemplateResponse(request, "student/set_review.html", {
        "user": user, "cls": cls, "set": s,
        "review_items": review_items, "score": score,
        "n_total": n_total, "n_done": n_done})


@router.get("/classes/{cid}/my-answers")
async def my_answers(cid: int, request: Request,
                     user: User = Depends(student_dep),
                     db: Session = Depends(get_db)):
    """我的答题记录：按试卷分组，只展示该学生在本班答过的题（只读）。"""
    cls = _ensure_enrolled(db, cid, user)
    sets = (db.query(ProblemSet)
            .filter(ProblemSet.class_id == cid)
            .order_by(ProblemSet.created_at).all())
    groups = []
    total_answered = total_correct = total_score = 0
    for s in sets:
        # 该试卷下该学生已答的题（JOIN 取到作答记录），按答题时间排序
        rows = (db.query(Question, Answer)
                .join(Answer, Answer.question_id == Question.id)
                .filter(Question.set_id == s.id,
                        Answer.student_id == user.id)
                .order_by(Answer.answered_at).all())
        records = []
        set_score = 0
        for q, ans in rows:
            records.append({"q": q, "ans": ans})
            set_score += ans.points_awarded
        if records:  # 只展示有答题记录的试卷
            n_correct = sum(1 for _, a in rows if a.is_correct)
            groups.append({
                "set": s, "records": records, "score": set_score,
                "n_done": len(records), "n_correct": n_correct,
            })
            total_answered += len(records)
            total_correct += n_correct
            total_score += set_score
    return templates.TemplateResponse(request, "student/my_answers.html", {
        "user": user, "cls": cls, "groups": groups,
        "total_answered": total_answered,
        "total_correct": total_correct,
        "total_score": total_score,
    })
