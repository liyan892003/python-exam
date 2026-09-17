"""把本地 SQLite(dev.db) 的全部数据迁移到云端 Postgres（Render）。

特性：
  - 保留所有主键 ID、外键关系、学生账号与答题记录
  - 枚举以字符串值写入（模型已用 native_enum=False 的 VARCHAR 列）
  - 迁移后重置 PG 自增序列，避免后续插入主键冲突
  - 目标库非空时拒绝执行（除非加 --force），防误覆盖

用法（在项目根目录、激活 venv 后）：
  export TARGET_DATABASE_URL="postgresql://用户:密码@主机/db?sslmode=require"
  python -m scripts.migrate_to_pg            # 源默认 ./dev.db
  python -m scripts.migrate_to_pg --force    # 目标已有数据也继续（先清空再导入）

TARGET_DATABASE_URL 在 Render 数据库面板的「External Database URL」复制。
"""
import os
import sys
from datetime import datetime

from sqlalchemy import create_engine, MetaData, Table, text

# 表导入顺序必须满足外键依赖
TABLE_ORDER = [
    "users",
    "classes",
    "problem_sets",
    "enrollments",
    "questions",
    "choices",
    "answers",
]

DATETIME_FMTS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S")


def _to_pg_value(col, v):
    """把 SQLite 读出的原始值转成适合 PG 绑定的 Python 值。"""
    if v is None:
        return None
    # SQLite 裸读日期是字符串，转成 datetime 让 PG 正确绑定
    if isinstance(v, str) and col.type.__class__.__name__ == "DateTime":
        for fmt in DATETIME_FMTS:
            try:
                return datetime.strptime(v, fmt)
            except ValueError:
                continue
    # SQLite BOOLEAN 存 0/1
    if col.type.__class__.__name__ == "Boolean" and isinstance(v, int):
        return bool(v)
    return v


def main() -> None:
    force = "--force" in sys.argv
    src_path = os.environ.get("SOURCE_DB", "dev.db")
    target_url = os.environ.get("TARGET_DATABASE_URL", "").strip()
    if not target_url:
        sys.exit("❌ 请先设置环境变量 TARGET_DATABASE_URL（Render 数据库的 External URL）")
    if not os.path.exists(src_path):
        sys.exit(f"❌ 找不到源数据库 {src_path}")

    src_engine = create_engine(f"sqlite:///{src_path}")
    dst_engine = create_engine(target_url, pool_pre_ping=True)

    # 用反射拿到两边真实的表结构（源库）
    src_meta = MetaData()
    src_meta.reflect(bind=src_engine)

    # 目标库：用应用模型建表（空表），再反射拿到带类型的 Table
    print("→ 确保目标库表结构存在（create_all）...")
    from app.db import Base  # noqa: E402
    import app.models  # noqa: F401,E402  注册所有模型
    Base.metadata.create_all(dst_engine)

    dst_meta = MetaData()
    dst_meta.reflect(bind=dst_engine)

    with src_engine.connect() as src_conn, dst_engine.begin() as dst_conn:
        # 安全检查
        users_t = dst_meta.tables["users"]
        existing = dst_conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
        if existing and not force:
            sys.exit(f"❌ 目标库已有 {existing} 个用户。确认要覆盖请加 --force（会先清空所有表）")
        if existing and force:
            print(f"→ --force：清空目标库 {len(TABLE_ORDER)} 张表...")
            for name in reversed(TABLE_ORDER):
                dst_conn.execute(dst_meta.tables[name].delete())

        # 临时关闭外键检查，按序导入
        dst_conn.execute(text("SET CONSTRAINTS ALL DEFERRED"))
        total = 0
        for name in TABLE_ORDER:
            if name not in src_meta.tables:
                print(f"  · {name}: 源库无此表，跳过")
                continue
            src_t = src_meta.tables[name]
            dst_t = dst_meta.tables[name]
            rows = [dict(r._mapping) for r in src_conn.execute(src_t.select()).all()]
            converted = []
            for row in rows:
                converted.append({
                    c.name: _to_pg_value(dst_t.c[c.name], row.get(c.name))
                    for c in dst_t.columns if c.name in row
                })
            if converted:
                dst_conn.execute(dst_t.insert(), converted)
            print(f"  · {name}: 导入 {len(converted)} 行")
            total += len(converted)

        # 重置自增序列，保证后续新数据的 id 不冲突
        print("→ 重置自增序列...")
        for name in TABLE_ORDER:
            maxid = dst_conn.execute(text(f"SELECT COALESCE(MAX(id),0) FROM {name}")).scalar()
            if maxid:
                dst_conn.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{name}','id'), {maxid}, true)"
                ))
            else:
                dst_conn.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{name}','id'), 1, false)"
                ))

    print(f"\n✅ 迁移完成，共导入 {total} 行数据。")
    print("   现在打开 Render 网站，用原有账号（邮箱/学号 + 密码）登录验证。")


if __name__ == "__main__":
    main()
