"""数据库引擎与会话工厂。SQLite/Postgres 双适配。"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from .config import get_settings

settings = get_settings()

_is_sqlite = settings.DATABASE_URL.startswith("sqlite")
db_url = settings.DATABASE_URL
# Render 提供的 DATABASE_URL 往往以 postgres:// 开头，SQLAlchemy 2.0 推荐显式指定驱动
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg2://", 1)
elif db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)

# SQLite 需要 check_same_thread=False（FastAPI 多线程）
connect_args = {"check_same_thread": False} if _is_sqlite else {}
# Postgres 设连接池，避免并发下连接耗尽
engine_kwargs = {"pool_pre_ping": True}
if not _is_sqlite:
    engine_kwargs.update(pool_size=8, max_overflow=4, pool_recycle=300)
engine = create_engine(db_url, connect_args=connect_args, **engine_kwargs)

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
