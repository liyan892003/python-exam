"""认证路由：注册、登录、登出。"""
from fastapi import APIRouter, Depends, Request, Response, Form, HTTPException, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import templates, get_current_user
from ..models import User, Role
from ..security import hash_password, verify_password
from ..schemas import RegisterIn, LoginIn

router = APIRouter()


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


def _redirect_home(user: User):
    target = "/teacher" if user.role == Role.teacher else "/student"
    return Response(status_code=303, headers={"Location": target})
