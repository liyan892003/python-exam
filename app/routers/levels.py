"""闯关路径：教师管理关卡；学生沿路径顺序解锁、AI 判定拼图点亮。

- 关卡 Level（第 N 周）含若干试卷(ProblemSet) 与知识点拼图(LevelKp，带教学要求)。
- 顺序解锁：第 1 关常开；其后关卡需上一关试卷全部答完。
- 拼图点亮：
  · know（了解）：答过 ≥1 题即亮
  · understand/master（理解/掌握）：进关卡页时按需调用 AI 严格判定（结果缓存），
    仅 verdict=mastered 点亮，partial 半亮；API 失败自动降级规则判定。
"""
import hashlib
import threading

from fastapi import APIRouter, Depends, Request, Response, Form, HTTPException
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..deps import templates, require_role
from ..models import (User, Class, Enrollment, ProblemSet, Question, Choice,
                      Answer, KnowledgePoint, Level, LevelKp, MasteryJudge,
                      PracticeAttempt, question_kps, Role)
from ..ai import judge_mastery

# 正在后台 AI 判定中的 (student_id, kp_id)，去重防止重复触发
_judging_lock = threading.Lock()
_judging: set[tuple[int, int]] = set()

t_router = APIRouter(prefix="/teacher/classes", tags=["teacher-levels"])
teacher_dep = require_role(Role.teacher)

s_router = APIRouter(prefix="/student/classes", tags=["student-levels"])
student_dep = require_role(Role.student)

REQ_LABELS = {"know": "了解", "understand": "理解", "master": "掌握"}


# ============================== 通用辅助 ==============================

def _owned_class(db: Session, cid: int, user: User) -> Class:
    cls = db.query(Class).filter(Class.id == cid, Class.teacher_id == user.id).first()
    if not cls:
        raise HTTPException(404, "班级不存在")
    return cls


def _enrolled_class(db: Session, cid: int, user: User) -> Class:
    en = db.query(Enrollment).filter(
        Enrollment.student_id == user.id, Enrollment.class_id == cid).first()
    if not en:
        raise HTTPException(403, "你未加入该班级")
    cls = db.query(Class).filter(Class.id == cid).first()
    if not cls:
        raise HTTPException(404, "班级不存在")
    return cls


def _class_levels(db: Session, cid: int) -> list[Level]:
    return (db.query(Level).filter(Level.class_id == cid)
            .order_by(Level.position, Level.id).all())


def _level_progress(db: Session, uid: int, level: Level) -> dict:
    """学生在该关卡的答题进度（基于关卡内试卷的题目）。"""
    qids = []
    for s in level.sets:
        qids += [q.id for q in s.questions]
    qids = list(set(qids))
    if not qids:
        return {"n_total": 0, "n_done": 0, "complete": False}
    n_done = db.query(Answer).filter(
        Answer.student_id == uid, Answer.question_id.in_(qids)).count()
    return {"n_total": len(qids), "n_done": n_done,
            "complete": n_done == len(qids)}


def _puzzle_states(db: Session, cid: int, uid: int, level: Level,
                   judges: dict | None = None) -> list[dict]:
    """关卡拼图的即时状态（只读缓存，不调用 AI）。"""
    out = []
    for lk in level.kp_links:
        kp = lk.kp
        if lk.requirement == "know":
            qs = _kp_questions(db, cid, kp.id)
            state = "lit" if _kp_records(db, uid, qs) else "dark"
        else:
            v = (judges or {}).get(lk.kp_id)
            state = "lit" if v == "mastered" else ("half" if v == "partial" else "dark")
        out.append({"kp_id": kp.id, "emoji": kp.emoji, "name": kp.name,
                    "req": lk.requirement, "state": state})
    return out


def level_path_view(db: Session, cid: int, user: User) -> dict:
    """学生关卡路径视图数据（class 页使用）。"""
    levels = _class_levels(db, cid)
    prev_complete = True
    current_idx = None
    items = []
    all_kp_ids = list({lk.kp_id for lv in levels for lk in lv.kp_links})
    judges = {j.kp_id: j.verdict for j in db.query(MasteryJudge).filter(
        MasteryJudge.student_id == user.id,
        MasteryJudge.kp_id.in_(all_kp_ids)).all()} if all_kp_ids else {}
    total_pieces = 0
    lit_pieces = 0
    for i, lv in enumerate(levels):
        p = _level_progress(db, user.id, lv)
        unlocked = prev_complete
        if unlocked and not p["complete"] and current_idx is None:
            current_idx = i
        pieces = _puzzle_states(db, cid, user.id, lv, judges)
        n_lit = sum(1 for x in pieces if x["state"] == "lit")
        total_pieces += len(pieces)
        lit_pieces += n_lit
        items.append({"level": lv, "prog": p, "unlocked": unlocked,
                      "n_kps": len(lv.kp_links), "pieces": pieces,
                      "n_lit": n_lit})
        prev_complete = p["complete"]
    if current_idx is None and items and items[-1]["prog"]["complete"]:
        current_idx = len(items) - 1  # 全部通关，小人停在终点
    # 成就徽章（进入路径时顺手评定一次，覆盖各种历史数据场景）
    from ..achievements import evaluate_achievements, unlocked_codes, next_goal
    from ..badges import BADGES, TIERS, TIER_ORDER
    fresh = evaluate_achievements(db, user)
    unlocked = unlocked_codes(db, user)
    tiers = []
    for tk in TIER_ORDER:
        t = TIERS[tk]
        tiers.append({
            "key": tk, "name": t["name"], "en": t["en"], "color": t["color"],
            "items": [{"code": b[0], "tier": b[1], "em": b[2], "en_name": b[3],
                       "zh": b[4], "desc": b[5], "unlocked": b[0] in unlocked}
                      for b in BADGES if b[1] == tk],
        })
    return {"items": items, "current_idx": current_idx,
            "total_pieces": total_pieces, "lit_pieces": lit_pieces,
            "badge_tiers": tiers, "n_badges": len(unlocked),
            "n_badges_total": len(BADGES), "fresh_badges": fresh,
            "next_hint": next_goal(db, user)}


def _kp_questions(db: Session, cid: int, kp_id: int) -> list[Question]:
    return (db.query(Question)
            .join(question_kps, question_kps.c.question_id == Question.id)
            .filter(Question.class_id == cid, question_kps.c.kp_id == kp_id)
            .order_by(Question.id).all())


def _kp_records(db: Session, uid: int, questions: list[Question]) -> list[dict]:
    """汇总每题：首答对错、最好成绩、次数、学生选择/正确答案文本。"""
    records = []
    for q in questions:
        exam = db.query(Answer).filter(
            Answer.student_id == uid, Answer.question_id == q.id).first()
        prac = db.query(PracticeAttempt).filter(
            PracticeAttempt.student_id == uid,
            PracticeAttempt.question_id == q.id).order_by(PracticeAttempt.id).all()
        if not exam and not prac:
            continue
        first = exam if exam else (prac[0] if prac else None)
        best = bool(exam and exam.is_correct) or any(a.is_correct for a in prac)
        attempts = (1 if exam else 0) + len(prac)
        picked_choice = None
        if exam:
            picked_choice = db.query(Choice).filter(Choice.id == exam.choice_id).first()
        elif prac:
            picked_choice = db.query(Choice).filter(Choice.id == prac[-1].choice_id).first()
        correct_choice = next((c for c in q.choices if c.is_correct), None)
        records.append({
            "stem": q.stem,
            "picked": picked_choice.text if picked_choice else "（未作答）",
            "correct_text": correct_choice.text if correct_choice else "—",
            "first_correct": bool(first and first.is_correct),
            "best_correct": best,
            "attempts": attempts,
        })
    return records


def _records_sig(records: list[dict]) -> str:
    raw = "|".join(f"{r['first_correct']}{r['best_correct']}{r['attempts']}{r['picked']}"
                   for r in records)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _do_judge_background(student_id: int, kp_id: int, kp_name: str,
                         requirement: str, sig: str,
                         records: list[dict], n_total: int) -> None:
    """后台线程：调用 AI 判定并写库，失败也不影响主请求。"""
    key = (student_id, kp_id)
    try:
        res = judge_mastery(kp_name, requirement, records, n_total=n_total)
        # 硬闸门：理解/掌握级至少答过 2 题，否则 AI 不能判 mastered
        need = min(2, n_total)
        if res["verdict"] == "mastered" and len(records) < need:
            res["verdict"] = "partial"
            res["reason"] = f"只做了 {len(records)}/{n_total} 题，再多练几道确认掌握"
        db = SessionLocal()
        try:
            judge = db.query(MasteryJudge).filter(
                MasteryJudge.student_id == student_id,
                MasteryJudge.kp_id == kp_id).first()
            if not judge:
                judge = MasteryJudge(student_id=student_id, kp_id=kp_id)
                db.add(judge)
            judge.verdict = res["verdict"]
            judge.reason = res["reason"]
            judge.source = res["source"]
            judge.sig = sig
            db.commit()
        finally:
            db.close()
    except Exception:
        pass  # 静默失败，下次请求会再次触发
    finally:
        with _judging_lock:
            _judging.discard(key)


def judge_level_puzzles(db: Session, cid: int, user: User, level: Level) -> list[dict]:
    """进关卡页：对每个拼图按需判定。AI 判定在后台线程异步执行，
    未就绪时先返回 dark +「分析中」，避免并发下阻塞请求导致崩溃。"""
    out = []
    for lk in level.kp_links:
        kp = lk.kp
        questions = _kp_questions(db, cid, kp.id)
        records = _kp_records(db, user.id, questions)
        sig = _records_sig(records)
        verdict, reason, source = None, "", ""
        pending = False
        if lk.requirement in ("understand", "master") and records:
            judge = db.query(MasteryJudge).filter(
                MasteryJudge.student_id == user.id,
                MasteryJudge.kp_id == kp.id).first()
            if judge and judge.sig == sig:
                verdict, reason, source = judge.verdict, judge.reason, judge.source
            else:
                # 需要重新判定：丢给后台线程，本次返回「分析中」
                key = (user.id, kp.id)
                with _judging_lock:
                    if key not in _judging:
                        _judging.add(key)
                        threading.Thread(
                            target=_do_judge_background,
                            args=(user.id, kp.id, kp.name, lk.requirement,
                                  sig, records, len(questions)),
                            daemon=True).start()
                pending = True
        attempted = len(records)
        if lk.requirement == "know":
            state = "lit" if attempted else "dark"
        elif pending:
            state = "dark"
            reason = "AI 分析中，刷新页面查看结果"
        elif verdict == "mastered":
            state = "lit"
        elif verdict == "partial":
            state = "half"
        else:
            state = "dark"
        out.append({"lk": lk, "kp": kp, "state": state, "reason": reason,
                    "source": source, "attempted": attempted,
                    "n_q": len(questions)})
    return out


# ============================== 教师端 ==============================

@t_router.post("/{cid}/levels")
async def create_level(cid: int, name: str = Form(...),
                       topic: str = Form(""),
                       description: str = Form(""),
                       user: User = Depends(teacher_dep),
                       db: Session = Depends(get_db)):
    cls = _owned_class(db, cid, user)
    name = name.strip()
    if not name:
        raise HTTPException(400, "关卡名不能为空")
    n = len(_class_levels(db, cid))
    db.add(Level(class_id=cls.id, name=name, topic=topic.strip()[:32],
                 description=description.strip(), position=n))
    db.commit()
    return Response(status_code=303,
                    headers={"Location": f"/teacher/classes/{cid}"})


@t_router.get("/{cid}/levels/{lid}")
async def level_manage(cid: int, lid: int, request: Request,
                       user: User = Depends(teacher_dep),
                       db: Session = Depends(get_db)):
    cls = _owned_class(db, cid, user)
    lv = db.query(Level).filter(Level.id == lid, Level.class_id == cid).first()
    if not lv:
        raise HTTPException(404, "关卡不存在")
    sets = (db.query(ProblemSet).filter(ProblemSet.class_id == cid)
            .order_by(ProblemSet.created_at).all())
    linked_set_ids = {s.id for s in lv.sets}
    chapters = (db.query(KnowledgePoint)
                .filter(KnowledgePoint.teacher_id == user.id,
                        KnowledgePoint.parent_id.is_(None))
                .order_by(KnowledgePoint.position).all())
    # 试卷归属单元：按卷内题目标签所属章节的众数归类，无标签归「未分组」
    UNGROUPED = "未分组"
    set_unit = {}
    for s in sets:
        votes: dict[str, int] = {}
        for q in s.questions:
            for kp in q.kps:
                unit = kp.parent.name if kp.parent_id else kp.name
                votes[unit] = votes.get(unit, 0) + 1
        set_unit[s.id] = max(votes, key=votes.get) if votes else UNGROUPED
    unit_names = [c.name for c in chapters] + [UNGROUPED]
    linked_map = {lk.kp_id: lk.requirement for lk in lv.kp_links}
    return templates.TemplateResponse(request, "teacher/level_manage.html", {
        "user": user, "cls": cls, "lv": lv, "sets": sets,
        "linked_set_ids": linked_set_ids, "chapters": chapters,
        "set_unit": set_unit, "unit_names": unit_names,
        "linked_map": linked_map, "req_labels": REQ_LABELS})


@t_router.post("/{cid}/levels/{lid}/edit")
async def edit_level(cid: int, lid: int, name: str = Form(...),
                     topic: str = Form(""),
                     description: str = Form(""),
                     user: User = Depends(teacher_dep),
                     db: Session = Depends(get_db)):
    _owned_class(db, cid, user)
    lv = db.query(Level).filter(Level.id == lid, Level.class_id == cid).first()
    if not lv:
        raise HTTPException(404, "关卡不存在")
    lv.name = name.strip() or lv.name
    lv.topic = topic.strip()[:32]
    lv.description = description.strip()
    db.commit()
    return Response(status_code=303,
                    headers={"Location": f"/teacher/classes/{cid}/levels/{lid}"})


@t_router.post("/{cid}/levels/{lid}/sets")
async def assign_sets(cid: int, lid: int,
                      set_ids: list[int] = Form(default=[]),
                      user: User = Depends(teacher_dep),
                      db: Session = Depends(get_db)):
    _owned_class(db, cid, user)
    lv = db.query(Level).filter(Level.id == lid, Level.class_id == cid).first()
    if not lv:
        raise HTTPException(404, "关卡不存在")
    valid = (db.query(ProblemSet)
             .filter(ProblemSet.class_id == cid,
                     ProblemSet.id.in_(set_ids)).all()) if set_ids else []
    lv.sets = valid
    db.commit()
    return Response(status_code=303,
                    headers={"Location": f"/teacher/classes/{cid}/levels/{lid}"})


def _redirect_manage(cid: int, lid: int) -> Response:
    return Response(status_code=303,
                    headers={"Location": f"/teacher/classes/{cid}/levels/{lid}"})


@t_router.post("/{cid}/levels/{lid}/sets/add")
async def add_set(cid: int, lid: int, set_id: int = Form(...),
                  user: User = Depends(teacher_dep),
                  db: Session = Depends(get_db)):
    _owned_class(db, cid, user)
    lv = db.query(Level).filter(Level.id == lid, Level.class_id == cid).first()
    if not lv:
        raise HTTPException(404, "关卡不存在")
    ps = db.query(ProblemSet).filter(ProblemSet.id == set_id,
                                     ProblemSet.class_id == cid).first()
    if ps and ps not in lv.sets:
        lv.sets.append(ps)
        db.commit()
    return _redirect_manage(cid, lid)


@t_router.post("/{cid}/levels/{lid}/sets/remove")
async def remove_set(cid: int, lid: int, set_id: int = Form(...),
                     user: User = Depends(teacher_dep),
                     db: Session = Depends(get_db)):
    _owned_class(db, cid, user)
    lv = db.query(Level).filter(Level.id == lid, Level.class_id == cid).first()
    if not lv:
        raise HTTPException(404, "关卡不存在")
    lv.sets = [s for s in lv.sets if s.id != set_id]
    db.commit()
    return _redirect_manage(cid, lid)


@t_router.post("/{cid}/levels/{lid}/kps")
async def assign_kps(cid: int, lid: int, request: Request,
                     user: User = Depends(teacher_dep),
                     db: Session = Depends(get_db)):
    """挂载知识点拼图；每个知识点的要求级别由动态字段 req_{kp_id} 提供。"""
    _owned_class(db, cid, user)
    lv = db.query(Level).filter(Level.id == lid, Level.class_id == cid).first()
    if not lv:
        raise HTTPException(404, "关卡不存在")
    form = await request.form()
    raw_ids = form.getlist("kp_ids")
    valid_kps = (db.query(KnowledgePoint)
                 .filter(KnowledgePoint.teacher_id == user.id,
                         KnowledgePoint.parent_id.is_not(None),
                         KnowledgePoint.id.in_([int(k) for k in raw_ids])).all()) \
                if raw_ids else []
    valid_id_set = {k.id for k in valid_kps}
    # 更新已有拼图的要求级别 / 新建
    kept = []
    for kp in valid_kps:
        req_value = form.get(f"req_{kp.id}", "understand")
        if req_value not in REQ_LABELS:
            req_value = "understand"
        lk = next((x for x in lv.kp_links if x.kp_id == kp.id), None)
        if lk:
            lk.requirement = req_value
        else:
            lk = LevelKp(level_id=lv.id, kp_id=kp.id, requirement=req_value)
            db.add(lk)
        kept.append(lk)
    # 删除被取消勾选的拼图
    for lk in list(lv.kp_links):
        if lk.kp_id not in valid_id_set:
            db.delete(lk)
    db.commit()
    return Response(status_code=303,
                    headers={"Location": f"/teacher/classes/{cid}/levels/{lid}"})


@t_router.post("/{cid}/levels/{lid}/kps/add")
async def add_kp_to_level(cid: int, lid: int, parent_id: int = Form(...),
                          name: str = Form(...), emoji: str = Form("⭐"),
                          user: User = Depends(teacher_dep),
                          db: Session = Depends(get_db)):
    """在指定章节下新增知识点，并自动挂到本关（要求=理解）。"""
    _owned_class(db, cid, user)
    lv = db.query(Level).filter(Level.id == lid, Level.class_id == cid).first()
    if not lv:
        raise HTTPException(404, "关卡不存在")
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
    kp = KnowledgePoint(teacher_id=user.id, parent_id=chapter.id, name=name,
                       emoji=(emoji.strip() or "⭐")[:8], position=n)
    db.add(kp)
    db.flush()
    lk = LevelKp(level_id=lv.id, kp_id=kp.id, requirement="understand")
    db.add(lk)
    db.commit()
    return Response(status_code=303,
                    headers={"Location": f"/teacher/classes/{cid}/levels/{lid}"})


@t_router.post("/{cid}/levels/{lid}/delete")
async def delete_level(cid: int, lid: int,
                       user: User = Depends(teacher_dep),
                       db: Session = Depends(get_db)):
    _owned_class(db, cid, user)
    lv = db.query(Level).filter(Level.id == lid, Level.class_id == cid).first()
    if lv:
        db.delete(lv)
        db.commit()
    return Response(status_code=303,
                    headers={"Location": f"/teacher/classes/{cid}"})


# ============================== 学生端 ==============================

@s_router.get("/{cid}/levels/{lid}")
async def student_level(cid: int, lid: int, request: Request,
                        user: User = Depends(student_dep),
                        db: Session = Depends(get_db)):
    cls = _enrolled_class(db, cid, user)
    lv = db.query(Level).filter(Level.id == lid, Level.class_id == cid).first()
    if not lv:
        raise HTTPException(404, "关卡不存在")
    path = level_path_view(db, cid, user)
    idx = next((i for i, it in enumerate(path["items"]) if it["level"].id == lid), -1)
    if idx > 0 and not path["items"][idx - 1]["prog"]["complete"]:
        raise HTTPException(403, "上一关还没有通关，先完成它吧！")
    puzzles = judge_level_puzzles(db, cid, user, lv)
    prog = _level_progress(db, user.id, lv)
    set_items = []
    for s in lv.sets:
        qids = [q.id for q in s.questions]
        n_done = db.query(Answer).filter(
            Answer.student_id == user.id,
            Answer.question_id.in_(qids)).count() if qids else 0
        set_items.append({"set": s, "n_total": len(qids), "n_done": n_done,
                          "complete": qids and n_done == len(qids)})
    n_lit = sum(1 for p in puzzles if p["state"] == "lit")
    from ..achievements import unlocked_codes
    from ..badges import A_BRONZE_CODES
    unlocked = unlocked_codes(db, user)
    # 集齐 8 条进阶线青铜徽章 = 全线入门，触发大庆祝
    all_done = set(A_BRONZE_CODES) <= unlocked
    return templates.TemplateResponse(request, "student/level_view.html", {
        "user": user, "cls": cls, "lv": lv, "puzzles": puzzles,
        "prog": prog, "set_items": set_items,
        "n_lit": n_lit, "n_pieces": len(puzzles),
        "all_done": all_done})
