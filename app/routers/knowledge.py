"""知识点地图 + 练习模式 + 错题本。

教师端（/teacher/knowledge）：
  预置大纲一键加载、章节/节点增删改；出题时给题目打知识点标签。

学生端（班级维度）：
  知识地图 → 节点详情 → 顺序练习（PracticeAttempt，不计考试成绩）
  手动「标记已掌握」点亮节点；全部点亮触发大动画
  错题本：考试答错且未被练习攻克的题，重做答对即移出错题本
"""
from fastapi import APIRouter, Depends, Request, Response, Form, HTTPException, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import templates, require_role
import hashlib

from ..models import (User, Class, Enrollment, Question, Choice, Answer,
                      KnowledgePoint, PracticeAttempt,
                      AbilityReport, question_kps, Role, QType, QuestionSource)
from ..presets import PRESET_KP_TREE
from ..ai import analyze_weakness, generate_mc_question, generate_tf_question

# —— 教师端 ——
t_router = APIRouter(prefix="/teacher/knowledge", tags=["teacher-knowledge"])
teacher_dep = require_role(Role.teacher)

# —— 学生端 ——
s_router = APIRouter(prefix="/student", tags=["student-knowledge"])
student_dep = require_role(Role.student)


# ============================== 教师端 ==============================

def _teacher_chapters(db: Session, user: User):
    return (db.query(KnowledgePoint)
            .filter(KnowledgePoint.teacher_id == user.id,
                    KnowledgePoint.parent_id.is_(None))
            .order_by(KnowledgePoint.position, KnowledgePoint.id).all())


@t_router.get("")
async def knowledge_admin(request: Request,
                          user: User = Depends(teacher_dep),
                          db: Session = Depends(get_db)):
    chapters = _teacher_chapters(db, user)
    has_any = len(chapters) > 0
    classes = (db.query(Class).filter(Class.teacher_id == user.id)
               .order_by(Class.created_at.desc()).all())
    # 每个节点附带该教师题库中打了此标签的题数
    return templates.TemplateResponse(request, "teacher/knowledge.html", {
        "user": user, "chapters": chapters, "has_any": has_any,
        "classes": classes})


@t_router.post("/{kid}/ai-generate")
async def ai_generate_for_kp(kid: int,
                             class_id: int = Form(...),
                             ai_type: str = Form("mc"),
                             difficulty: str = Form("basic"),
                             user: User = Depends(teacher_dep),
                             db: Session = Depends(get_db)):
    """按知识点 AI 出题：题目归入指定班级（不挂试卷），并打上该知识点标签。"""
    kp = _get_owned_kp(db, kid, user)
    if kp.parent_id is None:
        raise HTTPException(400, "请在具体知识点节点上出题，而非章节")
    cls = db.query(Class).filter(Class.id == class_id,
                                 Class.teacher_id == user.id).first()
    if not cls:
        raise HTTPException(404, "班级不存在")
    diff = difficulty if difficulty in ("basic", "advanced", "challenge") else "basic"
    try:
        if ai_type == "tf":
            data = generate_tf_question(topic=kp.name)
            q = Question(class_id=cls.id, set_id=None, q_type=QType.tf,
                         stem=data["stem"], explanation=data["explanation"],
                         source=QuestionSource.ai, difficulty=diff)
            db.add(q); db.flush()
            db.add(Choice(question_id=q.id, text="对", is_correct=bool(data["is_correct"])))
            db.add(Choice(question_id=q.id, text="错", is_correct=not bool(data["is_correct"])))
        else:
            out = generate_mc_question(topic=kp.name)
            q = Question(class_id=cls.id, set_id=None, q_type=QType.mc,
                         stem=out.stem, explanation=out.explanation,
                         source=QuestionSource.ai, difficulty=diff)
            db.add(q); db.flush()
            for c in out.choices:
                db.add(Choice(question_id=q.id, text=c.text, is_correct=c.is_correct))
        q.kps = [kp]
        db.commit()
    except Exception as e:
        raise HTTPException(400, f"AI 出题失败：{e}。请检查 DEEPSEEK_API_KEY 配置。")
    return Response(status_code=303, headers={"Location": "/teacher/knowledge"})


@t_router.post("/preset")
async def load_preset(user: User = Depends(teacher_dep),
                      db: Session = Depends(get_db)):
    """一键加载预置大纲（已有任何章节则拒绝，避免重复加载）。"""
    if _teacher_chapters(db, user):
        raise HTTPException(400, "你已有知识点大纲，不能重复加载预置大纲")
    for ci, (c_emoji, c_name, _nodes) in enumerate(PRESET_KP_TREE):
        chapter = KnowledgePoint(teacher_id=user.id, parent_id=None, name=c_name,
                                 emoji=c_emoji, position=ci, is_preset=True)
        db.add(chapter)
        db.flush()
        for ni, (emoji, name, desc) in enumerate(_nodes):
            db.add(KnowledgePoint(teacher_id=user.id, parent_id=chapter.id,
                                  name=name, description=desc, emoji=emoji,
                                  position=ni, is_preset=True))
    db.commit()
    return Response(status_code=303, headers={"Location": "/teacher/knowledge"})


@t_router.post("/chapters")
async def create_chapter(name: str = Form(...), emoji: str = Form("📚"),
                         user: User = Depends(teacher_dep),
                         db: Session = Depends(get_db)):
    name = name.strip()
    if not name:
        raise HTTPException(400, "章节名不能为空")
    n = len(_teacher_chapters(db, user))
    db.add(KnowledgePoint(teacher_id=user.id, parent_id=None, name=name,
                          emoji=(emoji.strip() or "📚")[:8], position=n))
    db.commit()
    return Response(status_code=303, headers={"Location": "/teacher/knowledge"})


@t_router.post("/nodes")
async def create_node(parent_id: int = Form(...), name: str = Form(...),
                      emoji: str = Form("⭐"), description: str = Form(""),
                      user: User = Depends(teacher_dep),
                      db: Session = Depends(get_db)):
    chapter = db.query(KnowledgePoint).filter(
        KnowledgePoint.id == parent_id,
        KnowledgePoint.teacher_id == user.id,
        KnowledgePoint.parent_id.is_(None)).first()
    if not chapter:
        raise HTTPException(404, "章节不存在")
    name = name.strip()
    if not name:
        raise HTTPException(400, "知识点名不能为空")
    n = len(chapter.children)
    db.add(KnowledgePoint(teacher_id=user.id, parent_id=chapter.id, name=name,
                          description=description.strip(),
                          emoji=(emoji.strip() or "⭐")[:8], position=n))
    db.commit()
    return Response(status_code=303,
                    headers={"Location": "/teacher/knowledge"})


def _get_owned_kp(db: Session, kid: int, user: User) -> KnowledgePoint:
    kp = db.query(KnowledgePoint).filter(
        KnowledgePoint.id == kid, KnowledgePoint.teacher_id == user.id).first()
    if not kp:
        raise HTTPException(404, "知识点不存在")
    return kp


@t_router.post("/{kid}/edit")
async def edit_kp(kid: int, name: str = Form(...), emoji: str = Form("⭐"),
                  description: str = Form(""),
                  user: User = Depends(teacher_dep),
                  db: Session = Depends(get_db)):
    kp = _get_owned_kp(db, kid, user)
    name = name.strip()
    if not name:
        raise HTTPException(400, "名称不能为空")
    kp.name = name
    kp.emoji = (emoji.strip() or ("📚" if kp.parent_id is None else "⭐"))[:8]
    kp.description = description.strip()
    db.commit()
    return Response(status_code=303,
                    headers={"Location": "/teacher/knowledge"})


@t_router.post("/{kid}/delete")
async def delete_kp(kid: int,
                    user: User = Depends(teacher_dep),
                    db: Session = Depends(get_db)):
    kp = _get_owned_kp(db, kid, user)
    db.delete(kp)  # 章节级联删除子节点；标签关联/掌握记录随外键 CASCADE
    db.commit()
    return Response(status_code=303,
                    headers={"Location": "/teacher/knowledge"})


# ============================== 学生端 ==============================

def _ensure_enrolled(db: Session, cid: int, user: User) -> Class:
    en = db.query(Enrollment).filter(
        Enrollment.student_id == user.id, Enrollment.class_id == cid).first()
    if not en:
        raise HTTPException(403, "你未加入该班级")
    cls = db.query(Class).filter(Class.id == cid).first()
    if not cls:
        raise HTTPException(404, "班级不存在")
    return cls


def _class_kp_questions(db: Session, cid: int, kp_id: int):
    """该班级下打了某知识点标签的题（固定顺序，练习池）。"""
    return (db.query(Question)
            .join(question_kps, question_kps.c.question_id == Question.id)
            .filter(Question.class_id == cid, question_kps.c.kp_id == kp_id)
            .order_by(Question.id).all())


def _attempt_stats(db: Session, uid: int, qids: list[int]):
    """练习统计：(练过的不同题数, 曾答对的不同题数, 答对过的题 id 集合)。"""
    if not qids:
        return 0, 0, set()
    attempts = db.query(PracticeAttempt).filter(
        PracticeAttempt.student_id == uid,
        PracticeAttempt.question_id.in_(qids)).all()
    attempted = {a.question_id for a in attempts}
    correct = {a.question_id for a in attempts if a.is_correct}
    return len(attempted), len(correct), correct


def _resolved_qids(db: Session, uid: int, qids: list[int]) -> set[int]:
    """错题本「已攻克」：存在正确练习记录的题。"""
    if not qids:
        return set()
    rows = (db.query(PracticeAttempt.question_id)
            .filter(PracticeAttempt.student_id == uid,
                    PracticeAttempt.is_correct.is_(True),
                    PracticeAttempt.question_id.in_(qids))
            .distinct().all())
    return {r[0] for r in rows}


# —— 星级规则：按答题数量自动点亮，共 3 星 ——
STAR_RULES = [(2, "练过 2 题"), (3, "答对 3 题"), (5, "答对 5 题")]


def kp_stars(n_attempted: int, n_correct: int) -> int:
    """1星=练过≥2题；2星=答对≥3题；3星=答对≥5题。"""
    if n_correct >= 5:
        return 3
    if n_correct >= 3:
        return 2
    if n_attempted >= 2:
        return 1
    return 0


@s_router.get("/classes/{cid}/knowledge")
async def knowledge_map(cid: int, request: Request,
                        user: User = Depends(student_dep),
                        db: Session = Depends(get_db)):
    cls = _ensure_enrolled(db, cid, user)
    chapters = (db.query(KnowledgePoint)
                .filter(KnowledgePoint.teacher_id == cls.teacher_id,
                        KnowledgePoint.parent_id.is_(None))
                .order_by(KnowledgePoint.position, KnowledgePoint.id).all())
    # 批量查询：一次拿到该班级所有题的 kp 关联，避免每个知识点单独查（N+1）
    kp_qids: dict[int, list[int]] = {}
    rows = (db.query(question_kps.c.kp_id, Question.id)
            .join(Question, Question.id == question_kps.c.question_id)
            .filter(Question.class_id == cid).all())
    for kp_id, qid in rows:
        kp_qids.setdefault(kp_id, []).append(qid)
    all_qids = list({qid for qids in kp_qids.values() for qid in qids})
    # 一次拿到该学生在这些题上的所有练习记录
    att_rows = db.query(PracticeAttempt.question_id, PracticeAttempt.is_correct).filter(
        PracticeAttempt.student_id == user.id,
        PracticeAttempt.question_id.in_(all_qids)).all() if all_qids else []
    attempted_map: dict[int, set[int]] = {}
    correct_map: dict[int, set[int]] = {}
    for qid, ok in att_rows:
        attempted_map.setdefault(qid, set()).add(qid)
        if ok:
            correct_map.setdefault(qid, set()).add(qid)

    total_leaf = 0
    total_stars = 0
    view = []
    for ch in chapters:
        nodes = []
        for kp in ch.children:
            total_leaf += 1
            qids = kp_qids.get(kp.id, [])
            n_q = len(qids)
            attempted = set()
            correct = set()
            for q in qids:
                if q in attempted_map:
                    attempted.add(q)
                if q in correct_map:
                    correct.add(q)
            n_attempted = len(attempted)
            n_correct = len(correct)
            stars = kp_stars(n_attempted, n_correct)
            total_stars += stars
            nodes.append({"kp": kp, "stars": stars, "n_q": n_q,
                          "n_attempted": n_attempted, "n_correct": n_correct})
        view.append({"chapter": ch, "nodes": nodes,
                     "n_stars": sum(n["stars"] for n in nodes)})
    max_stars = total_leaf * 3
    pct = int(total_stars / max_stars * 100) if max_stars else 0
    return templates.TemplateResponse(request, "student/knowledge_map.html", {
        "user": user, "cls": cls, "view": view,
        "total_leaf": total_leaf, "total_stars": total_stars,
        "max_stars": max_stars,
        "pct": pct, "all_done": max_stars > 0 and total_stars == max_stars})


def _back_context(request: Request, cid: int) -> tuple[str, str, str]:
    """解析来源页，返回 (back_url, back_label, from_query)。
    从关卡知识点列表进入时返回关卡页，否则返回知识地图。"""
    src = request.query_params.get("from", "map")
    lid = request.query_params.get("lid", "")
    if src == "level" and lid.isdigit():
        url = f"/student/classes/{cid}/levels/{lid}"
        return url, "返回本关", f"from=level&lid={lid}"
    url = f"/student/classes/{cid}/knowledge"
    return url, "返回知识地图", ""


@s_router.get("/classes/{cid}/knowledge/{kid}")
async def kp_detail(cid: int, kid: int, request: Request,
                    user: User = Depends(student_dep),
                    db: Session = Depends(get_db)):
    cls = _ensure_enrolled(db, cid, user)
    kp = db.query(KnowledgePoint).filter(
        KnowledgePoint.id == kid,
        KnowledgePoint.teacher_id == cls.teacher_id).first()
    if not kp:
        raise HTTPException(404, "知识点不存在")
    pool = _class_kp_questions(db, cid, kid)
    qids = [q.id for q in pool]
    n_attempted, n_correct, correct_ids = _attempt_stats(db, user.id, qids)
    resolved = _resolved_qids(db, user.id, qids)
    n_wrong = len([q for q in pool
                   if _is_exam_wrong(db, user.id, q.id) and q.id not in resolved])
    stars = kp_stars(n_attempted, n_correct)
    back_url, back_label, from_query = _back_context(request, cid)
    return templates.TemplateResponse(request, "student/kp_detail.html", {
        "user": user, "cls": cls, "kp": kp, "chapter": kp.parent,
        "n_q": len(pool), "n_attempted": n_attempted, "n_correct": n_correct,
        "n_wrong": n_wrong, "stars": stars,
        "back_url": back_url, "back_label": back_label,
        "from_query": from_query})


def _is_exam_wrong(db: Session, uid: int, qid: int) -> bool:
    a = db.query(Answer).filter(Answer.student_id == uid,
                                Answer.question_id == qid).first()
    return bool(a and not a.is_correct)


def _record_practice(db: Session, user: User, q: Question, choice_id: int):
    ch = db.query(Choice).filter(Choice.id == choice_id,
                                 Choice.question_id == q.id).first()
    if not ch:
        raise HTTPException(400, "选项无效")
    att = PracticeAttempt(student_id=user.id, question_id=q.id,
                          choice_id=ch.id, is_correct=ch.is_correct)
    db.add(att)
    db.commit()
    db.refresh(att)
    from ..achievements import evaluate_achievements
    evaluate_achievements(db, user)
    correct_choice = next((c for c in q.choices if c.is_correct), None)
    return {"picked": ch, "correct_choice": correct_choice, "att": att}


@s_router.get("/classes/{cid}/knowledge/{kid}/practice")
async def practice_get(cid: int, kid: int, request: Request,
                       idx: int = 0,
                       from_src: str = Query("map", alias="from"),
                       lid: str = "",
                       user: User = Depends(student_dep),
                       db: Session = Depends(get_db)):
    cls = _ensure_enrolled(db, cid, user)
    kp = db.query(KnowledgePoint).filter(
        KnowledgePoint.id == kid,
        KnowledgePoint.teacher_id == cls.teacher_id).first()
    if not kp:
        raise HTTPException(404, "知识点不存在")
    from_query = f"from={from_src}&lid={lid}" if from_src == "level" and lid.isdigit() else ""
    pool = _class_kp_questions(db, cid, kid)
    if not pool:
        return templates.TemplateResponse(request, "student/practice.html", {
            "user": user, "cls": cls, "kp": kp, "q": None,
            "mode": "kp", "idx": 0, "n_total": 0, "feedback": None,
            "from_query": from_query, "from_src": from_src, "from_lid": lid})
    idx = max(0, min(idx, len(pool) - 1))
    return templates.TemplateResponse(request, "student/practice.html", {
        "user": user, "cls": cls, "kp": kp, "q": pool[idx],
        "mode": "kp", "idx": idx, "n_total": len(pool), "feedback": None,
        "from_query": from_query, "from_src": from_src, "from_lid": lid})


@s_router.post("/classes/{cid}/knowledge/{kid}/practice")
async def practice_post(cid: int, kid: int, request: Request,
                        idx: int = Form(0), choice_id: int = Form(...),
                        from_src: str = Form("map"), lid: str = Form(""),
                        user: User = Depends(student_dep),
                        db: Session = Depends(get_db)):
    cls = _ensure_enrolled(db, cid, user)
    kp = db.query(KnowledgePoint).filter(
        KnowledgePoint.id == kid,
        KnowledgePoint.teacher_id == cls.teacher_id).first()
    pool = _class_kp_questions(db, cid, kid)
    if not pool or kp is None:
        raise HTTPException(404, "练习题不存在")
    idx = max(0, min(idx, len(pool) - 1))
    q = pool[idx]
    fb = _record_practice(db, user, q, choice_id)
    from_query = f"from={from_src}&lid={lid}" if from_src == "level" and lid.isdigit() else ""
    return templates.TemplateResponse(request, "student/practice.html", {
        "user": user, "cls": cls, "kp": kp, "q": q,
        "mode": "kp", "idx": idx, "n_total": len(pool), "feedback": fb,
        "from_query": from_query, "from_src": from_src, "from_lid": lid})


# —— 错题本 ——

@s_router.get("/classes/{cid}/wrong-book")
async def wrong_book(cid: int, request: Request,
                     user: User = Depends(student_dep),
                     db: Session = Depends(get_db)):
    cls = _ensure_enrolled(db, cid, user)
    # 本班考试答错的题（含学生选择）
    rows = (db.query(Answer, Question, Choice)
            .join(Question, Question.id == Answer.question_id)
            .outerjoin(Choice, Answer.choice_id == Choice.id)
            .filter(Question.class_id == cid,
                    Answer.student_id == user.id,
                    Answer.is_correct.is_(False))
            .order_by(Answer.answered_at.desc()).all())
    qids = list({q.id for _a, q, _c in rows})
    resolved = _resolved_qids(db, user.id, qids)
    # 按知识点分组（一题多个知识点取第一个；没打标签进「未分类」）
    groups: dict[str, list] = {}
    order: list[str] = []
    n_total_wrong = 0
    for a, q, picked in rows:
        if q.id in resolved:
            continue  # 已攻克 → 移出错题本
        n_total_wrong += 1
        kp = sorted(q.kps, key=lambda k: (k.parent.position if k.parent else 0,
                                          k.position))[:1]
        key = f"kp_{kp[0].id}" if kp else "none"
        title = kp[0].emoji + " " + kp[0].name if kp else "📎 未分类知识点"
        if key not in groups:
            groups[key] = {"title": title, "items": []}
            order.append(key)
        item = {"q": q, "ans": a, "picked": picked}
        groups[key]["items"].append(item)
    group_list = [groups[k] for k in order]
    # 历史已攻克数量（顶部鼓励）
    n_resolved = len([qid for qid in qids if qid in resolved])

    # AI 学情总结（按错题指纹缓存，攻克/新增错题才重新判定）
    report = None
    if n_total_wrong:
        wrong_payload = []
        for g in group_list:
            for it in g["items"]:
                qq = it["q"]
                correct_ch = next((c for c in qq.choices if c.is_correct), None)
                wrong_payload.append({
                    "stem": qq.stem,
                    "picked": it["picked"].text if it["picked"] else "（未作答）",
                    "correct_text": correct_ch.text if correct_ch else "—",
                    "kp_names": [k.name for k in qq.kps],
                    "explanation": qq.explanation or "",
                })
        sig_raw = "|".join(f"{w['stem']}{w['picked']}" for w in wrong_payload)
        sig = hashlib.sha256(sig_raw.encode()).hexdigest()[:32]
        cached = db.query(AbilityReport).filter(
            AbilityReport.student_id == user.id,
            AbilityReport.class_id == cid).first()
        if not cached or cached.sig != sig:
            res = analyze_weakness(wrong_payload, n_resolved)
            if not cached:
                cached = AbilityReport(student_id=user.id, class_id=cid)
                db.add(cached)
            cached.sig = sig
            cached.summary = res["summary"]
            cached.problems = res["problems"]
            cached.advice = res["advice"]
            cached.labels = "||".join(res["labels"])
            cached.source = res["source"]
            db.commit()
        report = cached

    return templates.TemplateResponse(request, "student/wrong_book.html", {
        "user": user, "cls": cls, "groups": group_list,
        "n_wrong": n_total_wrong, "n_resolved": n_resolved,
        "report": report})


@s_router.get("/classes/{cid}/wrong-book/retry/{qid}")
async def retry_get(cid: int, qid: int, request: Request,
                    user: User = Depends(student_dep),
                    db: Session = Depends(get_db)):
    cls = _ensure_enrolled(db, cid, user)
    q = db.query(Question).filter(Question.id == qid,
                                  Question.class_id == cid).first()
    if not q:
        raise HTTPException(404, "题目不存在")
    return templates.TemplateResponse(request, "student/practice.html", {
        "user": user, "cls": cls, "kp": None, "q": q,
        "mode": "retry", "idx": 0, "n_total": 1, "feedback": None})


@s_router.post("/classes/{cid}/wrong-book/retry/{qid}")
async def retry_post(cid: int, qid: int, request: Request,
                     choice_id: int = Form(...),
                     user: User = Depends(student_dep),
                     db: Session = Depends(get_db)):
    cls = _ensure_enrolled(db, cid, user)
    q = db.query(Question).filter(Question.id == qid,
                                  Question.class_id == cid).first()
    if not q:
        raise HTTPException(404, "题目不存在")
    fb = _record_practice(db, user, q, choice_id)
    return templates.TemplateResponse(request, "student/practice.html", {
        "user": user, "cls": cls, "kp": None, "q": q,
        "mode": "retry", "idx": 0, "n_total": 1, "feedback": fb})
