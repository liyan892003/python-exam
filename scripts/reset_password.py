"""直接改数据库里的账号密码。

忘记密码、又没设 PASSWORD_RESET_KEY（或密钥也忘了）时的救援手段。

用法（在项目根目录、激活 venv 后）：
  # 改本地 dev.db
  python -m scripts.reset_password yl_yanli@cufe.edu.cn '新密码'

  # 改 Render 云端库（External Database URL 从 Render 数据库面板复制）
  TARGET_DATABASE_URL="postgresql://用户:密码@主机/库?sslmode=require" \
    python -m scripts.reset_password yl_yanli@cufe.edu.cn '新密码'

不设 TARGET_DATABASE_URL 时默认操作本地 ./dev.db。
密码建议用单引号包住，避免 shell 把特殊字符吃掉。
"""
import os
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import User
from app.security import hash_password


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    email, new_password = sys.argv[1].strip(), sys.argv[2]
    if len(new_password) < 6:
        sys.exit("❌ 密码至少 6 位")

    url = os.environ.get("TARGET_DATABASE_URL", "").strip() or "sqlite:///./dev.db"
    where = "云端 Postgres" if not url.startswith("sqlite") else url
    print(f"→ 目标库：{where}")

    engine = create_engine(url, pool_pre_ping=True)
    Base.metadata.create_all(engine)  # 库为空时也能建表，避免报错看不懂
    db = sessionmaker(bind=engine)()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            sys.exit(f"❌ 没找到学号/工号 {email}")
        user.password_hash = hash_password(new_password)
        db.commit()
        print(f"✅ 已重置 {user.username}（{user.email}）的密码")
    finally:
        db.close()


if __name__ == "__main__":
    main()
