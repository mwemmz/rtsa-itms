"""Highway Code assistant: ask a road-rules question, get an answer with the rules cited."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.ratelimit import check_rate_limit, record_failure
from app.core.security import get_current_user
from app.models.user import User
from app.services import assistant, highway_code

router = APIRouter(prefix="/api/assistant", tags=["Assistant"])


class Turn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=1500)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=assistant.MAX_QUESTION)
    history: list[Turn] = Field(default_factory=list, max_length=12)


@router.get("/status")
def status(current_user: User = Depends(get_current_user)):
    index = highway_code.load_index()
    return {"enabled": settings.ASSISTANT_ENABLED, "loaded": index is not None,
            "rules": index.n if index else 0, "ai": assistant.ai_enabled()}


@router.post("/ask")
def ask(body: AskRequest, current_user: User = Depends(get_current_user)):
    if not settings.ASSISTANT_ENABLED:
        raise HTTPException(status_code=503, detail="The assistant is switched off")
    key = f"assistant:{current_user.id}"
    if not check_rate_limit(key, settings.ASSISTANT_RATE_PER_5MIN):
        raise HTTPException(status_code=429, detail="Too many questions. Wait a few minutes and try again.")
    record_failure(key)  # counts every request, not only failures
    return assistant.answer_question(body.question, [t.model_dump() for t in body.history])
