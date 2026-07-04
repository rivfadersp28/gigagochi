from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.schemas import AnonymousUserResponse

router = APIRouter(prefix="/users", tags=["users"])
DbSession = Annotated[Session, Depends(get_db)]


@router.post("/anonymous", response_model=AnonymousUserResponse)
def create_anonymous_user(db: DbSession) -> User:
    user = User()
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
