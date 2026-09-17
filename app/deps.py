"""FastAPI 依赖：当前用户、角色拦截、模板渲染辅助。"""
from fastapi import Depends, HTTPException, Request, status
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import User, Role

settings = get_settings()

templates = Jinja2Templates(directory="app/templates")


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    """从 session 取当前登录用户；未登录返回 None。"""
    uid = request.session.get("uid")
    if not uid:
        return None
    return db.query(User).filter(User.id == uid).first()


def require_login(user: User | None = Depends(get_current_user)) -> User:
    if not user:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER,
                            headers={"Location": "/login"})
    return user


def require_role(role: Role):
    """角色依赖工厂：require_role(Role.teacher) → 必须是教师。"""
    def _dep(user: User = Depends(require_login)) -> User:
        if user.role != role:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="无权限访问该页面")
        return user
    return _dep
