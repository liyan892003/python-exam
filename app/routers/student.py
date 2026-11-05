"""学生端路由：加入班级、按试卷顺序答题（即时反馈）、查分。

答题流：
  班级页 → 显示该班级的试卷列表（不显示题目列表）
  点「开始/继续」某套试卷 → 跳到该试卷第一道未答题
  答完一题 → answer_result 页自动 3 秒后跳到下一题
  试卷全部答完 → 自动跳转该班级排行榜（含烟花/奖杯动画）
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response, Form, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, selectinload
from starlette.concurrency import run_in_threadpool

from ..db import get_db
from ..deps import templates, require_role
from ..models import (User, Class, Enrollment, ProblemSet, Question, Choice, Answer,
                      Role, GameSession, GameStatus, GamePlayer, GameGuess,
                      GroupTask, GroupTeam, GroupMember, GroupSubmission, GroupShowcase)
from ..achievements import evaluate_achievements
from .. import number_guess, ai

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


# —— 游戏区：单人小游戏 + 课堂多人游戏 ——
@router.get("/games")
async def games_hub(request: Request, user: User = Depends(student_dep),
                    db: Session = Depends(get_db)):
    # 该生所在班级里「等待加入 / 进行中」的课堂游戏
    rows = (db.query(GameSession, Class)
            .join(Class, Class.id == GameSession.class_id)
            .join(Enrollment, Enrollment.class_id == Class.id)
            .filter(Enrollment.student_id == user.id,
                    GameSession.status.in_([GameStatus.lobby, GameStatus.playing]))
            .order_by(GameSession.created_at.desc()).all())
    games = []
    for session, cls in rows:
        joined = any(p.student_id == user.id for p in session.players)
        games.append({"session": session, "cls": cls, "joined": joined,
                      "n_players": len(session.players)})
    return templates.TemplateResponse(request, "student/games.html", {
        "user": user, "games": games})


@router.get("/games/number-guess")
async def number_guess_solo(request: Request, user: User = Depends(student_dep)):
    return templates.TemplateResponse(request, "student/number_guess.html", {"user": user})


def _get_visible_session(db: Session, sid: int, user: User) -> GameSession:
    """取该生所在班级的一局游戏（否则 403）。"""
    session = (db.query(GameSession)
               .options(selectinload(GameSession.players).selectinload(GamePlayer.student),
                        selectinload(GameSession.guesses).selectinload(GameGuess.student))
               .filter(GameSession.id == sid).first())
    if not session:
        raise HTTPException(404, "游戏不存在")
    _ensure_enrolled(db, session.class_id, user)
    return session


@router.get("/games/sessions/{sid}")
async def game_play(sid: int, request: Request,
                    user: User = Depends(student_dep),
                    db: Session = Depends(get_db)):
    session = _get_visible_session(db, sid, user)
    # 等待阶段进入即加入；重新取一次以带出预加载（commit 后对象已过期）
    number_guess.add_player(db, session, user.id)
    session = _get_visible_session(db, sid, user)
    return templates.TemplateResponse(request, "student/game_play.html", {
        "user": user, "session": session, "cls": session.class_,
        "state": number_guess.session_state(session, user.id)})


@router.get("/games/sessions/{sid}/state")
async def game_play_state(sid: int,
                          user: User = Depends(student_dep),
                          db: Session = Depends(get_db)):
    session = _get_visible_session(db, sid, user)
    return JSONResponse(number_guess.session_state(session, user.id))


@router.post("/games/sessions/{sid}/guess")
async def game_play_guess(sid: int, guess: int = Form(...),
                          user: User = Depends(student_dep),
                          db: Session = Depends(get_db)):
    session = _get_visible_session(db, sid, user)
    number_guess.submit_guess(db, session, user.id, guess)
    session = _get_visible_session(db, sid, user)
    return JSONResponse(number_guess.session_state(session, user.id))


# —— 分组作业：建组 / 入组 / 队长上传 / 查看展演 ——

TEXT_LIMIT = 200_000  # 单份材料上限 200KB


def _get_student_group_task(db: Session, tid: int, user: User) -> GroupTask:
    """取本班的分组任务（未选课 → 403）。"""
    task = (db.query(GroupTask)
            .options(selectinload(GroupTask.teams).selectinload(GroupTeam.members)
                     .selectinload(GroupMember.student),
                     selectinload(GroupTask.teams).selectinload(GroupTeam.leader),
                     selectinload(GroupTask.teams).selectinload(GroupTeam.submission),
                     selectinload(GroupTask.showcases).selectinload(GroupShowcase.team)
                     .selectinload(GroupTeam.submission),
                     selectinload(GroupTask.showcases).selectinload(GroupShowcase.team)
                     .selectinload(GroupTeam.members).selectinload(GroupMember.student))
            .filter(GroupTask.id == tid).first())
    if not task:
        raise HTTPException(404, "分组任务不存在")
    _ensure_enrolled(db, task.class_id, user)
    return task


def _my_team(task: GroupTask, uid: int) -> GroupTeam | None:
    for t in task.teams:
        if any(m.student_id == uid for m in t.members):
            return t
    return None


def _class_students(db: Session, cid: int) -> list[User]:
    return (db.query(User)
            .join(Enrollment, Enrollment.student_id == User.id)
            .filter(Enrollment.class_id == cid, User.role == Role.student)
            .order_by(User.email).all())


@router.get("/classes/{cid}/groups")
async def student_group_tasks(cid: int, request: Request,
                              user: User = Depends(student_dep),
                              db: Session = Depends(get_db)):
    cls = _ensure_enrolled(db, cid, user)
    tasks = (db.query(GroupTask)
             .options(selectinload(GroupTask.teams).selectinload(GroupTeam.members),
                      selectinload(GroupTask.teams).selectinload(GroupTeam.submission),
                      selectinload(GroupTask.showcases))
             .filter(GroupTask.class_id == cid)
             .order_by(GroupTask.created_at.desc()).all())
    rows = [{"task": t, "my_team": _my_team(t, user.id),
             "n_teams": len(t.teams),
             "n_submitted": sum(1 for tt in t.teams if tt.submission),
             "n_showcase": len(t.showcases)} for t in tasks]
    return templates.TemplateResponse(request, "student/group_tasks.html", {
        "user": user, "cls": cls, "rows": rows})


@router.get("/groups/tasks/{tid}")
async def student_group_task(tid: int, request: Request,
                             user: User = Depends(student_dep),
                             db: Session = Depends(get_db)):
    task = _get_student_group_task(db, tid, user)
    my_team = _my_team(task, user.id)
    taken_ids = {m.student_id for t in task.teams for m in t.members}
    classmates = [u for u in _class_students(db, task.class_id)
                  if u.id not in taken_ids and u.id != user.id]
    open_teams = [t for t in task.teams
                  if len(t.members) < task.max_members and t.id != (my_team.id if my_team else None)]
    return templates.TemplateResponse(request, "student/group_task.html", {
        "user": user, "cls": task.class_, "task": task, "my_team": my_team,
        "classmates": classmates, "open_teams": open_teams,
        "showcases": task.showcases,
        "scored": request.query_params.get("scored") == "1",
        "added": request.query_params.get("added") == "1",
        "left": request.query_params.get("left") == "1",
        "error": request.query_params.get("error")})


@router.post("/groups/tasks/{tid}/teams")
async def create_group_team(tid: int,
                            team_name: str = Form(...),
                            member_ids: list[int] = Form([]),
                            user: User = Depends(student_dep),
                            db: Session = Depends(get_db)):
    task = _get_student_group_task(db, tid, user)
    if _my_team(task, user.id):
        raise HTTPException(400, "你已经加入了一个小组，不能再新建")
    name = team_name.strip()
    if not name:
        raise HTTPException(400, "小组名称不能为空")
    ids: list[int] = []
    for sid in member_ids:
        if sid != user.id and sid not in ids:
            ids.append(sid)
    if len(ids) + 1 > task.max_members:
        raise HTTPException(400, f"每组最多 {task.max_members} 人（含队长）")
    enrolled = {u.id for u in _class_students(db, task.class_id)}
    taken = {m.student_id for t in task.teams for m in t.members}
    for sid in ids:
        if sid not in enrolled:
            raise HTTPException(400, "只能选择本班同学")
        if sid in taken:
            raise HTTPException(400, "有同学已经加入其他小组了")
    team = GroupTeam(task_id=task.id, name=name[:64], leader_id=user.id)
    db.add(team)
    db.flush()
    db.add(GroupMember(team_id=team.id, student_id=user.id))
    for sid in ids:
        db.add(GroupMember(team_id=team.id, student_id=sid))
    db.commit()
    return Response(status_code=303,
                    headers={"Location": f"/student/groups/tasks/{tid}"})


@router.post("/groups/tasks/{tid}/teams/{team_id}/join")
async def join_group_team(tid: int, team_id: int,
                          user: User = Depends(student_dep),
                          db: Session = Depends(get_db)):
    task = _get_student_group_task(db, tid, user)
    if _my_team(task, user.id):
        raise HTTPException(400, "你已经加入了一个小组，不能重复加入")
    team = next((t for t in task.teams if t.id == team_id), None)
    if not team:
        raise HTTPException(404, "小组不存在")
    if len(team.members) >= task.max_members:
        raise HTTPException(400, "该组人数已满，换一组吧")
    db.add(GroupMember(team_id=team.id, student_id=user.id))
    db.commit()
    return Response(status_code=303,
                    headers={"Location": f"/student/groups/tasks/{tid}"})


@router.post("/groups/tasks/{tid}/leave")
async def leave_group_team(tid: int,
                           user: User = Depends(student_dep),
                           db: Session = Depends(get_db)):
    """退出当前小组。队长退出时：有队员则转让队长，无队员则解散小组（提交材料一并删除）。"""
    task = _get_student_group_task(db, tid, user)
    team = _my_team(task, user.id)
    if not team:
        raise HTTPException(400, "你还没有加入小组")
    me = next((m for m in team.members if m.student_id == user.id), None)
    others = [m for m in team.members if m.student_id != user.id]
    if me:
        db.delete(me)
    if team.leader_id == user.id:
        if others:
            team.leader_id = others[0].student_id
        else:
            db.delete(team)
    db.commit()
    return Response(status_code=303,
                    headers={"Location": f"/student/groups/tasks/{tid}?left=1"})


@router.post("/groups/tasks/{tid}/teams/{team_id}/add")
async def add_team_member(tid: int, team_id: int,
                          student_id: int = Form(...),
                          user: User = Depends(student_dep),
                          db: Session = Depends(get_db)):
    """队长把还没有小组的本班同学直接加进本组。"""
    task = _get_student_group_task(db, tid, user)
    team = next((t for t in task.teams if t.id == team_id), None)
    if not team:
        raise HTTPException(404, "小组不存在")
    if team.leader_id != user.id:
        raise HTTPException(403, "只有队长可以添加同学")
    if len(team.members) >= task.max_members:
        raise HTTPException(400, f"本组已满 {task.max_members} 人")
    taken = {m.student_id for t in task.teams for m in t.members}
    if student_id in taken:
        raise HTTPException(400, "该同学已经在某个小组里了")
    if student_id not in {u.id for u in _class_students(db, task.class_id)}:
        raise HTTPException(400, "只能添加本班同学")
    db.add(GroupMember(team_id=team.id, student_id=student_id))
    db.commit()
    return Response(status_code=303,
                    headers={"Location": f"/student/groups/tasks/{tid}?added=1"})


@router.post("/groups/tasks/{tid}/submit")
async def submit_group_work(tid: int,
                            file: UploadFile | None = File(None),
                            content: str = Form(""),
                            problem: str = Form(""),
                            note: str = Form(""),
                            user: User = Depends(student_dep),
                            db: Session = Depends(get_db)):
    task = _get_student_group_task(db, tid, user)
    team = _my_team(task, user.id)
    if not team:
        raise HTTPException(400, "请先加入一个小组再提交")
    if team.leader_id != user.id:
        raise HTTPException(403, "只有队长可以上传材料")

    problem = problem.strip()[:600]
    allowed = [ln.strip() for ln in (task.problems or "").splitlines() if ln.strip()]
    if allowed:
        if problem not in allowed:
            raise HTTPException(400, "请从老师给出的问题中选择一个")
    elif not problem:
        raise HTTPException(400, "请先填写要解决的问题")

    filename = "粘贴文本"
    text = content.strip()
    if file is not None and file.filename:
        raw = await file.read()
        if len(raw) > TEXT_LIMIT:
            raise HTTPException(400, "文件太大了，上限 200KB")
        for enc in ("utf-8", "gbk"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise HTTPException(400, "只支持文本/代码文件（UTF-8 或 GBK）")
        filename = file.filename[:255]
    if not text.strip():
        raise HTTPException(400, "材料内容不能为空：上传文件或粘贴代码都可以")
    if len(text) > TEXT_LIMIT:
        raise HTTPException(400, "内容太长，上限 200KB")

    members = "、".join(m.student.username for m in team.members if m.student)
    result = await run_in_threadpool(
        ai.score_group_submission, task.title, task.description or "",
        team.name, members, filename, note.strip(), problem, text)
    feedback = result["comment"]
    if result.get("suggestions"):
        feedback += "\n改进建议：" + result["suggestions"]

    sub = team.submission
    if sub is None:
        sub = GroupSubmission(team_id=team.id, submitted_by=user.id)
        db.add(sub)
    sub.filename = filename
    sub.note = note.strip()[:500]
    sub.problem = problem
    sub.content = text
    sub.submitted_by = user.id
    sub.ai_score = result["score"]
    sub.ai_feedback = feedback
    sub.ai_solved = result.get("solved", "")
    sub.ai_out_of_syllabus = result.get("out_of_syllabus", "") or ""
    sub.ai_source = result.get("source", "fallback")
    sub.submitted_at = datetime.utcnow()
    db.commit()
    return Response(status_code=303,
                    headers={"Location": f"/student/groups/tasks/{tid}?scored=1"})


@router.get("/groups/tasks/{tid}/showcase")
async def student_group_showcase(tid: int, request: Request,
                                 user: User = Depends(student_dep),
                                 db: Session = Depends(get_db)):
    task = _get_student_group_task(db, tid, user)
    return templates.TemplateResponse(request, "student/group_showcase.html", {
        "user": user, "cls": task.class_, "task": task,
        "showcases": task.showcases})
