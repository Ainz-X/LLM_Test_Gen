from __future__ import annotations

import datetime as dt
import json
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import case
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Conversation, Message, MessageFeedback, ToolCall, User
from app.schemas import ChatRequest, ChatResponse, ConversationCreate, ConversationOut, ConversationUpdate, MessageFeedbackIn, MessageFeedbackOut, MessageOut
from app.security import get_current_user
from app.services.agent_service import AgentService


router = APIRouter(prefix="/chat", tags=["chat"])


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def safe_export_name(title: str, suffix: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", title.strip(), flags=re.UNICODE).strip("-")
    return f"{cleaned or 'conversation'}.{suffix}"


def owned_conversation(db: Session, user: User, conversation_id: str) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@router.post("/conversations", response_model=ConversationOut)
def create_conversation(payload: ConversationCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    conversation = Conversation(user_id=user.id, title=payload.title)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.query(Conversation).filter(Conversation.user_id == user.id).order_by(Conversation.updated_at.desc()).all()


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    conversation = owned_conversation(db, user, conversation_id)
    message_ids = [
        row.id
        for row in db.query(Message.id)
        .filter(Message.conversation_id == conversation_id)
        .all()
    ]
    if message_ids:
        db.query(MessageFeedback).filter(
            MessageFeedback.user_id == user.id,
            MessageFeedback.message_id.in_(message_ids),
        ).delete(synchronize_session=False)
    db.query(ToolCall).filter(
        ToolCall.user_id == user.id,
        ToolCall.conversation_id == conversation_id,
    ).delete(synchronize_session=False)
    db.delete(conversation)
    db.commit()
    return {"ok": True, "deleted_conversation_id": conversation_id}


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
def rename_conversation(
    conversation_id: str,
    payload: ConversationUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = owned_conversation(db, user, conversation_id)
    conversation.title = payload.title.strip()
    conversation.updated_at = dt.datetime.utcnow()
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


@router.get("/conversations/{conversation_id}/export")
def export_conversation(
    conversation_id: str,
    format: str = "markdown",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = owned_conversation(db, user, conversation_id)
    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(
            Message.created_at.asc(),
            case((Message.role == "user", 0), (Message.role == "assistant", 1), else_=2).asc(),
            Message.id.asc(),
        )
        .all()
    )
    export_format = format.lower()
    if export_format not in {"markdown", "json"}:
        raise HTTPException(status_code=400, detail="Unsupported export format")
    if export_format == "json":
        payload = {
            "conversation": {
                "id": conversation.id,
                "title": conversation.title,
                "active_file_id": conversation.active_file_id,
                "updated_at": conversation.updated_at.isoformat() if conversation.updated_at else None,
            },
            "messages": [
                {
                    "id": message.id,
                    "role": message.role,
                    "content": message.content,
                    "tool_results": message.tool_results,
                    "created_at": message.created_at.isoformat() if message.created_at else None,
                }
                for message in messages
            ],
        }
        return Response(
            json.dumps(payload, ensure_ascii=False, indent=2),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{safe_export_name(conversation.title, "json")}"'},
        )

    lines = [
        f"# {conversation.title}",
        "",
        f"- Conversation ID: `{conversation.id}`",
        f"- Exported at: `{dt.datetime.utcnow().isoformat()}Z`",
        "",
    ]
    for message in messages:
        role = "User" if message.role == "user" else "Assistant"
        created = message.created_at.isoformat() if message.created_at else ""
        lines.extend([f"## {role} - {created}", "", message.content or "", ""])
        items = (message.tool_results or {}).get("items") if isinstance(message.tool_results, dict) else None
        if items:
            lines.extend(["<details>", "<summary>Tool results</summary>", "", "```json", json.dumps(items, ensure_ascii=False, indent=2), "```", "", "</details>", ""])
    return Response(
        "\n".join(lines),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe_export_name(conversation.title, "md")}"'},
    )


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
def list_messages(conversation_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    owned_conversation(db, user, conversation_id)
    return (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(
            Message.created_at.asc(),
            case((Message.role == "user", 0), (Message.role == "assistant", 1), else_=2).asc(),
            Message.id.asc(),
        )
        .all()
    )


@router.post("", response_model=ChatResponse)
def chat(payload: ChatRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    conversation = db.get(Conversation, payload.conversation_id) if payload.conversation_id else None
    if conversation is None:
        conversation = Conversation(user_id=user.id, title=payload.message[:80] or "新对话")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
    if conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if payload.active_file_id:
        conversation.active_file_id = payload.active_file_id

    user_message = Message(conversation_id=conversation.id, role="user", content=payload.message)
    db.add(user_message)
    db.commit()

    history = [
        {"role": message.role, "content": message.content}
        for message in conversation.messages
        if message.role in {"user", "assistant"} and message.id != user_message.id
    ]
    service = AgentService(db, user)
    reply, tool_results = service.llm_chat(
        conversation.id,
        payload.message,
        payload.active_file_id or conversation.active_file_id,
        history,
        payload.selected_file_ids,
    )
    assistant_message = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=reply,
        tool_results={"items": tool_results},
    )
    conversation.updated_at = dt.datetime.utcnow()
    db.add(assistant_message)
    db.commit()
    return ChatResponse(conversation_id=conversation.id, reply=reply, tool_results=tool_results)


@router.post("/stream")
def chat_stream(payload: ChatRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    def generate():
        conversation = db.get(Conversation, payload.conversation_id) if payload.conversation_id else None
        if conversation is None:
            conversation = Conversation(user_id=user.id, title=payload.message[:80] or "新对话")
            db.add(conversation)
            db.commit()
            db.refresh(conversation)
        if conversation.user_id != user.id:
            yield sse("error", {"detail": "Conversation not found"})
            return
        if payload.active_file_id:
            conversation.active_file_id = payload.active_file_id

        user_message = Message(conversation_id=conversation.id, role="user", content=payload.message)
        db.add(user_message)
        db.commit()
        db.refresh(user_message)
        yield sse("meta", {"conversation_id": conversation.id, "user_message_id": user_message.id})
        yield sse("status", {"message": "Thinking"})

        history = [
            {"role": message.role, "content": message.content}
            for message in conversation.messages
            if message.role in {"user", "assistant"} and message.id != user_message.id
        ]
        service = AgentService(db, user)
        reply_parts: list[str] = []
        tool_results: list[dict] = []
        for event in service.llm_chat_events(
            conversation.id,
            payload.message,
            payload.active_file_id or conversation.active_file_id,
            history,
            payload.selected_file_ids,
        ):
            if event.get("event") == "tool":
                result = event["data"]
                tool_results.append(result)
                yield sse("tool", result)
            elif event.get("event") == "status":
                yield sse("status", {"message": str(event.get("message") or "处理中")})
            elif event.get("event") == "delta":
                text = str(event.get("text") or "")
                reply_parts.append(text)
                yield sse("delta", {"text": text})
        reply = "".join(reply_parts)
        assistant_message = Message(
            conversation_id=conversation.id,
            role="assistant",
            content=reply,
            tool_results={"items": tool_results},
        )
        conversation.updated_at = dt.datetime.utcnow()
        db.add(assistant_message)
        db.commit()
        db.refresh(assistant_message)
        yield sse(
            "done",
            {
                "conversation_id": conversation.id,
                "assistant_message_id": assistant_message.id,
                "tool_results": tool_results,
            },
        )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/messages/{message_id}/feedback", response_model=MessageFeedbackOut)
def rate_message(
    message_id: str,
    payload: MessageFeedbackIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    message = db.get(Message, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    conversation = db.get(Conversation, message.conversation_id)
    if not conversation or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Message not found")
    feedback = (
        db.query(MessageFeedback)
        .filter(MessageFeedback.user_id == user.id, MessageFeedback.message_id == message_id)
        .one_or_none()
    )
    if feedback is None:
        feedback = MessageFeedback(user_id=user.id, message_id=message_id, rating=payload.rating, note=payload.note)
        db.add(feedback)
    else:
        feedback.rating = payload.rating
        feedback.note = payload.note
        feedback.updated_at = dt.datetime.utcnow()
    db.commit()
    db.refresh(feedback)
    return feedback
