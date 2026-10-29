"""密码哈希：直接用 bcrypt 库（避免 passlib 与 bcrypt 5.0 的兼容问题）。

注意：bcrypt 是 CPU 密集型同步调用（本地实测约 250ms/次）。在 async 路由里直接
调用会阻塞事件循环——几十人同时登录时，连 /health 都响应不了，Render 会判定实例
不健康并重启（表现为「打开网页转圈很久 → 502/503」）。因此提供 async 包装，把
计算丢到线程池执行（bcrypt 的 C 实现会释放 GIL，可真正并行）。
"""
import bcrypt
from fastapi.concurrency import run_in_threadpool

# bcrypt cost=12 单次约 0.25s（纯 CPU）。Render starter 只有 0.5 CPU，全班 40 人
# 同时登录就是 10 个 CPU 秒 ≈ 20s 的等待。cost=10 约快 4 倍（仍是 OWASP 下限），
# 保证 40 人同时登录时等待在几秒内。已存在的旧哈希（cost=12）仍可正常校验，
# 因为校验耗时由哈希自身携带的 cost 决定。
BCRYPT_ROUNDS = 10


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"),
                         bcrypt.gensalt(BCRYPT_ROUNDS)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


async def hash_password_async(password: str) -> str:
    """在线程池里做 bcrypt 哈希，避免阻塞事件循环。"""
    return await run_in_threadpool(hash_password, password)


async def verify_password_async(plain: str, hashed: str) -> bool:
    """在线程池里做 bcrypt 校验，避免阻塞事件循环。"""
    return await run_in_threadpool(verify_password, plain, hashed)
