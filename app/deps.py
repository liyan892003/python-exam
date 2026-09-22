"""FastAPI 依赖：当前用户、角色拦截、模板渲染辅助。"""
from fastapi import Depends, HTTPException, Request, status
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import User, Role

settings = get_settings()

templates = Jinja2Templates(directory="app/templates")


def piece_path(left: str = "0", right: str = "0",
               top: str = "0", bottom: str = "0") -> str:
    """生成 100×100 拼图块的 SVG path（互锁结构）。

    每条边：'0'=平边（板块边缘），'t'=凸榫，'s'=凹槽；相邻块凹凸互补。
    凸榫/凹槽用直径 20 的半圆，凸块会越过边界 10px，SVG 需 overflow:visible。
    """
    d = ["M 0 0"]
    # 上边（左→右，凸向上 sweep=1）
    if top == "0":
        d.append("H 100")
    else:
        d += ["L 40 0", f"A 10 10 0 0 {'1' if top == 't' else '0'} 60 0", "L 100 0"]
    # 右边（上→下，凸向右 sweep=1）
    d.append("L 100 40")
    if right != "0":
        d.append(f"A 10 10 0 0 {'1' if right == 't' else '0'} 100 60")
    d.append("L 100 100")
    # 下边（右→左，凸向下 sweep=1）
    d.append("L 60 100")
    if bottom != "0":
        d.append(f"A 10 10 0 0 {'1' if bottom == 't' else '0'} 40 100")
    d.append("L 0 100")
    # 左边（下→上，凸向左 sweep=1）
    d.append("L 0 60")
    if left != "0":
        d.append(f"A 10 10 0 0 {'1' if left == 't' else '0'} 0 40")
    d.append("L 0 0 Z")
    return " ".join(d)


templates.env.globals["piece_path"] = piece_path

# 成就徽章渲染
from .badges import medal_svg, BADGES as _BADGES, TIERS as _TIERS, BADGE_MAP, TIER_ORDER
templates.env.globals["medal_svg"] = medal_svg
templates.env.globals["BADGES"] = _BADGES
templates.env.globals["BADGE_MAP"] = BADGE_MAP
templates.env.globals["TIERS"] = _TIERS
templates.env.globals["TIER_ORDER"] = TIER_ORDER


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
