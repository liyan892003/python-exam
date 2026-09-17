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
from .models import Class, ProblemSet, Question
from .routers import auth, teacher, student, leaderboard

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时自动建表
    Base.metadata.create_all(bind=engine)
    _migrate_to_sets()
    yield


app = FastAPI(title="Python 游戏化练习", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY,
                    same_site="lax", https_only=settings.is_prod)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(auth.router)
app.include_router(teacher.router)
app.include_router(student.router)
app.include_router(leaderboard.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def index(request: Request, user=Depends(get_current_user)):
    if user:
        target = "/teacher" if user.role.value == "teacher" else "/student"
        return RedirectResponse(target, status_code=303)
    return templates.TemplateResponse(request, "index.html", {"user": None})
