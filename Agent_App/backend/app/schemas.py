from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    display_name: str = ""


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str

    class Config:
        from_attributes = True


class ConversationCreate(BaseModel):
    title: str = "新对话"


class ConversationOut(BaseModel):
    id: str
    title: str
    active_file_id: str | None = None
    updated_at: dt.datetime

    class Config:
        from_attributes = True


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    tool_results: dict[str, Any] | None = None
    created_at: dt.datetime

    class Config:
        from_attributes = True


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str
    active_file_id: str | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    reply: str
    tool_results: list[dict[str, Any]] = []


class MessageFeedbackIn(BaseModel):
    rating: str = Field(pattern="^(up|down)$")
    note: str = ""


class MessageFeedbackOut(BaseModel):
    id: str
    message_id: str
    rating: str
    note: str
    updated_at: dt.datetime

    class Config:
        from_attributes = True


class UploadedFileOut(BaseModel):
    id: str
    original_name: str
    sha256: str
    size_bytes: int
    analysis: dict[str, Any]
    created_at: dt.datetime

    class Config:
        from_attributes = True


class BatchUploadOut(BaseModel):
    files: list[UploadedFileOut]
    rejected: list[dict[str, str]] = []


class BatchFileDeleteIn(BaseModel):
    file_ids: list[str] = Field(min_length=1, max_length=500)


class BatchFileDeleteOut(BaseModel):
    ok: bool
    deleted_file_ids: list[str]
    deleted_artifacts: int
    not_found: list[str] = []


class BatchGenerateIn(BaseModel):
    file_ids: list[str] | None = None
    only_missing: bool = True
    max_files: int = Field(default=50, ge=1, le=200)
    goal: str = "Generate JUnit 4 tests for all selected Java files."


class ArtifactOut(BaseModel):
    id: str
    file_id: str
    kind: str
    storage_path: str
    model: str
    metadata_json: dict[str, Any]
    created_at: dt.datetime

    class Config:
        from_attributes = True


class ArtifactReadOut(BaseModel):
    artifact: ArtifactOut
    code: str
