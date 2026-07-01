# routes/hub/hub_routes.py
"""Agent Hub — inbox and project board REST API."""

import uuid
import logging
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.database import SessionLocal, HubMessage, HubProject, HubCanon
from src.auth_helpers import require_authenticated_request
from src.constants import STATIC_DIR

logger = logging.getLogger(__name__)


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _short_id():
    return str(uuid.uuid4())[:8]


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class MessageSend(BaseModel):
    from_agent: str
    to_agent: str
    content: str
    related_project_id: Optional[str] = None


class ProjectUpsert(BaseModel):
    name: str
    status: str = "open"
    owner_agent: Optional[str] = None
    notes: Optional[str] = None


class ProjectPatch(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None
    owner_agent: Optional[str] = None


class CanonUpsert(BaseModel):
    series: str
    entity: str
    fact: str
    source_note: Optional[str] = None


# ---------------------------------------------------------------------------
# Route factory
# ---------------------------------------------------------------------------

def setup_hub_routes() -> APIRouter:
    router = APIRouter(prefix="/api/hub", tags=["hub"])

    # -----------------------------------------------------------------------
    # GUI — serve the static SPA
    # -----------------------------------------------------------------------

    @router.get("/ui", include_in_schema=False)
    @router.get("/ui/", include_in_schema=False)
    async def hub_ui(request: Request):
        require_authenticated_request(request)
        html_path = Path(STATIC_DIR) / "hub" / "index.html"
        if not html_path.exists():
            raise HTTPException(status_code=404, detail="Hub UI not found")
        return FileResponse(str(html_path), media_type="text/html")

    # -----------------------------------------------------------------------
    # Inbox
    # -----------------------------------------------------------------------

    @router.post("/inbox")
    async def send_message(body: MessageSend, request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            now = _utcnow()
            msg = HubMessage(
                id=_short_id(),
                from_agent=body.from_agent,
                to_agent=body.to_agent,
                content=body.content,
                related_project_id=body.related_project_id,
                created_at=now,
                updated_at=now,
            )
            db.add(msg)
            db.commit()
            db.refresh(msg)
            return _msg_dict(msg)
        finally:
            db.close()

    @router.get("/inbox/{agent_id}")
    async def get_inbox(agent_id: str, request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            msgs = (
                db.query(HubMessage)
                .filter(HubMessage.to_agent == agent_id)
                .order_by(HubMessage.read_at.asc().nullsfirst(), HubMessage.created_at.desc())
                .limit(50)
                .all()
            )
            return [_msg_dict(m) for m in msgs]
        finally:
            db.close()

    @router.patch("/inbox/{message_id}/read")
    async def mark_read(message_id: str, request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            msg = db.query(HubMessage).filter(HubMessage.id == message_id).first()
            if not msg:
                raise HTTPException(status_code=404, detail="Message not found")
            if not msg.read_at:
                msg.read_at = _utcnow()
                msg.updated_at = _utcnow()
                db.commit()
                db.refresh(msg)
            return _msg_dict(msg)
        finally:
            db.close()

    @router.get("/inbox/{agent_id}/unread-count")
    async def unread_count(agent_id: str, request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            count = (
                db.query(HubMessage)
                .filter(HubMessage.to_agent == agent_id, HubMessage.read_at.is_(None))
                .count()
            )
            return {"count": count}
        finally:
            db.close()

    # -----------------------------------------------------------------------
    # Projects
    # -----------------------------------------------------------------------

    @router.get("/projects")
    async def list_projects(request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            projects = (
                db.query(HubProject)
                .order_by(HubProject.updated_at.desc())
                .all()
            )
            return [_proj_dict(p) for p in projects]
        finally:
            db.close()

    @router.post("/projects")
    async def create_project(body: ProjectUpsert, request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            existing = db.query(HubProject).filter(HubProject.name == body.name).first()
            now = _utcnow()
            if existing:
                existing.status = body.status
                existing.owner_agent = body.owner_agent
                if body.notes is not None:
                    existing.notes = body.notes
                existing.updated_at = now
                db.commit()
                db.refresh(existing)
                return _proj_dict(existing)
            proj = HubProject(
                id=_short_id(),
                name=body.name,
                status=body.status,
                owner_agent=body.owner_agent,
                notes=body.notes,
                created_at=now,
                updated_at=now,
            )
            db.add(proj)
            db.commit()
            db.refresh(proj)
            return _proj_dict(proj)
        finally:
            db.close()

    @router.get("/projects/{name}")
    async def get_project(name: str, request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            proj = db.query(HubProject).filter(HubProject.name == name).first()
            if not proj:
                raise HTTPException(status_code=404, detail="Project not found")
            return _proj_dict(proj)
        finally:
            db.close()

    @router.patch("/projects/{name}")
    async def update_project(name: str, body: ProjectPatch, request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            proj = db.query(HubProject).filter(HubProject.name == name).first()
            if not proj:
                raise HTTPException(status_code=404, detail="Project not found")
            if body.status is not None:
                proj.status = body.status
            if body.notes is not None:
                proj.notes = body.notes
            if body.owner_agent is not None:
                proj.owner_agent = body.owner_agent
            proj.updated_at = _utcnow()
            db.commit()
            db.refresh(proj)
            return _proj_dict(proj)
        finally:
            db.close()

    # -----------------------------------------------------------------------
    # Canon
    # -----------------------------------------------------------------------

    @router.get("/canon/{series}")
    async def list_canon(series: str, request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            rows = (
                db.query(HubCanon)
                .filter(HubCanon.series == series)
                .order_by(HubCanon.entity)
                .all()
            )
            return [_canon_dict(r) for r in rows]
        finally:
            db.close()

    @router.get("/canon/{series}/{entity}")
    async def get_canon(series: str, entity: str, request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            row = (
                db.query(HubCanon)
                .filter(HubCanon.series == series, HubCanon.entity == entity)
                .first()
            )
            if not row:
                raise HTTPException(status_code=404, detail="Canon fact not found")
            return _canon_dict(row)
        finally:
            db.close()

    @router.post("/canon")
    async def upsert_canon(body: CanonUpsert, request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            now = _utcnow()
            row = (
                db.query(HubCanon)
                .filter(HubCanon.series == body.series, HubCanon.entity == body.entity)
                .first()
            )
            if row:
                row.fact = body.fact
                if body.source_note is not None:
                    row.source_note = body.source_note
                row.updated_at = now
                db.commit()
                db.refresh(row)
                return _canon_dict(row)
            row = HubCanon(
                id=_short_id(),
                series=body.series,
                entity=body.entity,
                fact=body.fact,
                source_note=body.source_note,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return _canon_dict(row)
        finally:
            db.close()

    @router.delete("/canon/{series}/{entity}")
    async def delete_canon(series: str, entity: str, request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            row = (
                db.query(HubCanon)
                .filter(HubCanon.series == series, HubCanon.entity == entity)
                .first()
            )
            if not row:
                raise HTTPException(status_code=404, detail="Canon fact not found")
            db.delete(row)
            db.commit()
            return {"deleted": True}
        finally:
            db.close()

    return router


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

def _msg_dict(m: HubMessage) -> dict:
    return {
        "id": m.id,
        "from_agent": m.from_agent,
        "to_agent": m.to_agent,
        "content": m.content,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "read_at": m.read_at.isoformat() if m.read_at else None,
        "related_project_id": m.related_project_id,
    }


def _proj_dict(p: HubProject) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "status": p.status,
        "owner_agent": p.owner_agent,
        "notes": p.notes,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def _canon_dict(r: HubCanon) -> dict:
    return {
        "id": r.id,
        "series": r.series,
        "entity": r.entity,
        "fact": r.fact,
        "source_note": r.source_note,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }
