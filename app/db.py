"""数据库引擎与会话工厂。SQLite/Postgres 双适配。"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from .config import get_settings

settings = get_settings()

_is_sqlite = settings.DATABASE_URL.startswith("sqlite")
# SQLite 需要 check_same_thread=False（FastAPI 多线程）
connect_args = {"check_same_thread": False} if _is_sqlite else {}
engine = create_engine(settings.DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)

if _is_sqlite:
    # SQLite 默认不强制外键，开启后 ondelete=CASCADE 才与 Postgres 行为一致
    @event.listens_for(engine, "connect")
    def _sqlite_fk(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI 依赖：每请求一个 DB 会话，请求结束自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
