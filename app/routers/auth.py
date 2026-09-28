"""认证路由：注册、登录、登出、修改密码、忘记密码重置。"""
import time

from fastapi import APIRouter, Depends, Request, Response, Form, HTTPException, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import templates, get_current_user
from ..models import User, Role
from ..security import hash_password, verify_password
from ..schemas import RegisterIn, LoginIn
from ..config import get_settings

router = APIRouter()

# —— /forgot 防爆破：同一 IP 连续失败 5 次锁定 10 分钟（内存级，重启清零） ——
ForgotState = tuple[int, float]  # (连续失败次数, 锁定截止时间戳)
_forgot_failures: dict[str, ForgotState] = {}
FORGOT_MAX_TRIES = 5
FORGOT_LOCK_SECONDS = 600


@router.get("/register")
async def register_page(request: Request, user: User | None = Depends(get_current_user)):
    if user:
        return _redirect_home(user)
    return templates.TemplateResponse(request, "auth/register.html",
                                     {"error": None})


@router.post("/register")
async def register(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form("student"),
    db: Session = Depends(get_db),
):
    try:
        data = RegisterIn(username=username, email=email, password=password, role=role)
    except Exception as e:
        return templates.TemplateResponse(
            request, "auth/register.html",
            {"error": f"输入不合法：{e}"}, status_code=400)

    if db.query(User).filter(User.email == data.email).first():
        return templates.TemplateResponse(
            request, "auth/register.html",
            {"error": "该学号/工号已注册"}, status_code=400)

    user = User(
        username=data.username, email=data.email,
        password_hash=hash_password(data.password),
        role=Role(data.role),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    # 自动登录
    request.session["uid"] = user.id
    return _redirect_home(user)


@router.get("/login")
async def login_page(request: Request, user: User | None = Depends(get_current_user)):
    if user:
        return _redirect_home(user)
    return templates.TemplateResponse(request, "auth/login.html",
                                     {"error": None})


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request, "auth/login.html",
            {"error": "学号/工号或密码错误"}, status_code=400)
    request.session["uid"] = user.id
    return _redirect_home(user)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return Response(status_code=303, headers={"Location": "/"})


# —— 登录后修改自己的密码 ——

@router.get("/account/password")
async def password_page(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse(request, "auth/password.html",
                                     {"user": user, "error": None, "ok": False})


@router.post("/account/password")
async def change_password(request: Request,
                          old_password: str = Form(...),
                          new_password: str = Form(...),
                          confirm: str = Form(...),
                          user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    if not verify_password(old_password, user.password_hash):
        return templates.TemplateResponse(
            request, "auth/password.html",
            {"user": user, "error": "当前密码不正确", "ok": False}, status_code=400)
    if len(new_password) < 6:
        return templates.TemplateResponse(
            request, "auth/password.html",
            {"user": user, "error": "新密码至少 6 位", "ok": False}, status_code=400)
    if new_password != confirm:
        return templates.TemplateResponse(
            request, "auth/password.html",
            {"user": user, "error": "两次输入的新密码不一致", "ok": False}, status_code=400)
    if new_password == old_password:
        return templates.TemplateResponse(
            request, "auth/password.html",
            {"user": user, "error": "新密码不能和当前密码一样", "ok": False}, status_code=400)
    user.password_hash = hash_password(new_password)
    db.commit()
    return templates.TemplateResponse(request, "auth/password.html",
                                     {"user": user, "error": None, "ok": True})


# —— 忘记密码：学号 + 重置密钥自助重置（无邮件服务下的找回通道） ——

def _forgot_locked(client_ip: str) -> float:
    """返回剩余锁定秒数；0 表示未锁定。顺带清理过期记录。"""
    st = _forgot_failures.get(client_ip)
    if not st:
        return 0.0
    _fails, locked_until = st
    if locked_until and time.time() < locked_until:
        return locked_until - time.time()
    if locked_until and time.time() >= locked_until:
        _forgot_failures.pop(client_ip, None)
    return 0.0


def _forgot_fail(client_ip: str) -> None:
    st = _forgot_failures.get(client_ip, (0, 0.0))
    fails = st[0] + 1
    locked_until = time.time() + FORGOT_LOCK_SECONDS if fails >= FORGOT_MAX_TRIES else 0.0
    _forgot_failures[client_ip] = (fails, locked_until)


@router.get("/forgot")
async def forgot_page(request: Request):
    key_enabled = bool(get_settings().PASSWORD_RESET_KEY)
    return templates.TemplateResponse(request, "auth/forgot.html", {
        "error": None, "ok_user": None, "key_enabled": key_enabled})


@router.post("/forgot")
async def forgot_reset(request: Request,
                       email: str = Form(...),
                       reset_key: str = Form(""),
                       new_password: str = Form(...),
                       confirm: str = Form(...),
                       db: Session = Depends(get_db)):
    settings = get_settings()
    client_ip = request.client.host if request.client else "?"
    if not settings.PASSWORD_RESET_KEY:
        return templates.TemplateResponse(
            request, "auth/forgot.html",
            {"error": "自助重置未开启：学生请联系老师重置密码；老师请在 Render 环境变量中设置 PASSWORD_RESET_KEY",
             "ok_user": None, "key_enabled": False}, status_code=400)
    remain = _forgot_locked(client_ip)
    if remain > 0:
        return templates.TemplateResponse(
            request, "auth/forgot.html",
            {"error": f"尝试次数过多，请 {int(remain // 60) + 1} 分钟后再试",
             "ok_user": None, "key_enabled": True}, status_code=429)
    if reset_key.strip() != settings.PASSWORD_RESET_KEY:
        _forgot_fail(client_ip)
        return templates.TemplateResponse(
            request, "auth/forgot.html",
            {"error": "重置密钥不正确", "ok_user": None, "key_enabled": True},
            status_code=400)
    user = db.query(User).filter(User.email == email.strip()).first()
    if not user:
        _forgot_fail(client_ip)
        return templates.TemplateResponse(
            request, "auth/forgot.html",
            {"error": "该学号/工号未注册", "ok_user": None, "key_enabled": True},
            status_code=400)
    if len(new_password) < 6:
        return templates.TemplateResponse(
            request, "auth/forgot.html",
            {"error": "新密码至少 6 位", "ok_user": None, "key_enabled": True},
            status_code=400)
    if new_password != confirm:
        return templates.TemplateResponse(
            request, "auth/forgot.html",
            {"error": "两次输入的新密码不一致", "ok_user": None, "key_enabled": True},
            status_code=400)
    user.password_hash = hash_password(new_password)
    db.commit()
    _forgot_failures.pop(client_ip, None)  # 成功后清零
    return templates.TemplateResponse(request, "auth/forgot.html", {
        "error": None, "ok_user": user.username, "key_enabled": True})


def _redirect_home(user: User):
    target = "/teacher" if user.role == Role.teacher else "/student"
    return Response(status_code=303, headers={"Location": target})
