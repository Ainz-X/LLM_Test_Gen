from __future__ import annotations

from fastapi import APIRouter, Depends

from app.models import User
from app.security import get_current_user
from app.services import a3_tools
from app.services.skills import DEFAULT_SKILL_REGISTRY


router = APIRouter(prefix="/workspace", tags=["workspace"])


@router.get("/inspect")
def inspect(project_key: str | None = None, user: User = Depends(get_current_user)):
    return a3_tools.inspect_workspace(project_key)


@router.post("/validate")
def validate(user: User = Depends(get_current_user)):
    return a3_tools.validate_workspace()


@router.get("/skills")
def skills(user: User = Depends(get_current_user)):
    return {"ok": True, "skills": DEFAULT_SKILL_REGISTRY.catalog()}
