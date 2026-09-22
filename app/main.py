"""FastAPI 应用入口。

启动时自动建表（create_all）+ ProblemSet 迁移，适合 SQLite/Postgres 双场景。
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from sqlalchemy import inspect, text
from starlette.middleware.sessions import SessionMiddleware

from .config import get_settings
from .db import Base, engine, SessionLocal
from .deps import templates, get_current_user
from .models import Class, ProblemSet, Question, Level
from .routers import auth, teacher, student, leaderboard, knowledge, levels

settings = get_settings()


def _migrate_to_sets() -> None:
    """旧库迁移：补 questions.set_id 列，并把孤儿题挂到自动创建的「默认试卷」上。"""
    inspector = inspect(engine)
    if "questions" not in inspector.get_table_names():
        return  # 全新库，create_all 后续会建好

    cols = {c["name"] for c in inspector.get_columns("questions")}
    if "set_id" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE questions ADD COLUMN set_id INTEGER"))

    db = SessionLocal()
    try:
        # 每个班级若没有任何试卷，先建一张「默认试卷」
        for cls in db.query(Class).all():
            default_set = (db.query(ProblemSet)
                           .filter(ProblemSet.class_id == cls.id)
                           .order_by(ProblemSet.created_at)
                           .first())
            if not default_set:
                default_set = ProblemSet(class_id=cls.id, name="默认试卷",
                                         description="迁移自旧版题目库")
                db.add(default_set)
                db.flush()
            # 把该班级里 set_id 为 NULL 的题目挂到默认试卷
            orphans = (db.query(Question)
                       .filter(Question.class_id == cls.id, Question.set_id.is_(None))
                       .all())
            for q in orphans:
                q.set_id = default_set.id
        db.commit()
    finally:
        db.close()


def _migrate_level_topics() -> None:
    """轻量迁移：为 levels 补 topic 列（create_all 不会给已存在的表加列）。"""
    inspector = inspect(engine)
    if "levels" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("levels")}
    if "topic" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE levels ADD COLUMN topic VARCHAR(32) DEFAULT ''"))


def _migrate_question_difficulty() -> None:
    """轻量迁移：为 questions 补 difficulty 列（默认 basic 基础题）。"""
    inspector = inspect(engine)
    if "questions" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("questions")}
    if "difficulty" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE questions ADD COLUMN difficulty VARCHAR(12) "
                              "NOT NULL DEFAULT 'basic'"))


def _migrate_to_levels() -> None:
    """旧库迁移：没有任何关卡的班级，自动建「第 1 周」并装入其全部试卷。"""
    db = SessionLocal()
    try:
        for cls in db.query(Class).all():
            if db.query(Level).filter(Level.class_id == cls.id).first():
                continue
            sets = (db.query(ProblemSet)
                    .filter(ProblemSet.class_id == cls.id)
                    .order_by(ProblemSet.created_at).all())
            lvl = Level(class_id=cls.id, name="第 1 周",
                        description="自动创建：请老师在班级管理中改名、调整与挂载知识点",
                        position=0)
            db.add(lvl)
            db.flush()
            lvl.sets = sets
        db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时自动建表
    Base.metadata.create_all(bind=engine)
    # 列迁移必须在任何 ORM 查询 Question 之前完成
    _migrate_question_difficulty()
    _migrate_to_sets()
    _migrate_level_topics()
    _migrate_to_levels()
    yield


app = FastAPI(title="Python 游戏化练习", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY,
                    same_site="lax", https_only=settings.is_prod)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(auth.router)
app.include_router(teacher.router)
app.include_router(student.router)
app.include_router(leaderboard.router)
app.include_router(knowledge.t_router)
app.include_router(knowledge.s_router)
app.include_router(levels.t_router)
app.include_router(levels.s_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def index(request: Request, user=Depends(get_current_user)):
    if user:
        target = "/teacher" if user.role.value == "teacher" else "/student"
        return RedirectResponse(target, status_code=303)
    return templates.TemplateResponse(request, "index.html", {"user": None})
