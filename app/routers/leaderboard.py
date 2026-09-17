"""排行榜路由：按班级总分排序，Top3 颁奖台。

支持 ?set=<set_id> 查询参数：当学生刚做完一套试卷过来时，顶部显示
「这套题得了 X 分」横幅，并提供「查看每题解析」按钮。
"""
from typing import Optional

from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import templates, get_current_user
from ..models import User, Enrollment, Class, ProblemSet, Question, Answer, Role
from ..models import class_leaderboard

router = APIRouter(tags=["leaderboard"])


@router.get("/classes/{cid}/leaderboard")
async def leaderboard(cid: int, request: Request,
                      set: Optional[int] = None,
                      user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    # 教师本人（班级所有者）或已加入的学生可见
    cls = db.query(Class).filter(Class.id == cid).first()
    if not cls:
        raise HTTPException(404, "班级不存在")
    if user.role == Role.student:
        en = db.query(Enrollment).filter(
            Enrollment.student_id == user.id, Enrollment.class_id == cid).first()
        if not en:
            raise HTTPException(403, "你未加入该班级")
    elif user.role == Role.teacher and cls.teacher_id != user.id:
        raise HTTPException(403, "无权限查看该班级")

    ranking = class_leaderboard(db, cid)
    n_students = len(ranking)

    # 当前学生排名（用于切换撒花/鼓励动画）
    my_rank = None
    if user.role == Role.student:
        for r in ranking:
            if r["user_id"] == user.id:
                my_rank = r["rank"]
                break

    # 试卷上下文：用于显示「这套题得了 X 分」横幅
    set_obj = None
    set_score = None
    if set is not None:
        set_obj = (db.query(ProblemSet)
                   .filter(ProblemSet.id == set, ProblemSet.class_id == cid).first())
        if set_obj and user.role == Role.student:
            set_questions = (db.query(Question)
                             .filter(Question.set_id == set_obj.id).all())
            qids = [q.id for q in set_questions]
            answers = (db.query(Answer).filter(
                Answer.student_id == user.id,
                Answer.question_id.in_(qids)).all()) if qids else []
            n_correct = sum(1 for a in answers if a.is_correct)
            n_total = len(set_questions)
            n_done = len(answers)
            pts = sum(a.points_awarded for a in answers)
            set_score = {"score": pts, "correct": n_correct,
                         "total": n_total, "done": n_done}

    return templates.TemplateResponse(request, "student/leaderboard.html", {
        "user": user, "cls": cls,
        "ranking": ranking, "n_students": n_students,
        "my_id": user.id if user.role == Role.student else None,
        "my_rank": my_rank,
        "set_obj": set_obj, "set_score": set_score})
