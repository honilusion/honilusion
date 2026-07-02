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

from core.database import SessionLocal, HubMessage, HubProject, HubCanon, HubProjectSection, HubProjectChangelog
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


class SectionUpdate(BaseModel):
    content: str
    updated_by: Optional[str] = None


class ChangelogAppend(BaseModel):
    entry: str
    created_by: Optional[str] = None


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

    @router.get("/inbox/agents")
    async def list_inbox_agents(request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            from sqlalchemy import union
            from sqlalchemy import select as sa_select
            froms = db.execute(
                sa_select(HubMessage.from_agent.label("agent")).distinct()
            ).scalars().all()
            tos = db.execute(
                sa_select(HubMessage.to_agent.label("agent")).distinct()
            ).scalars().all()
            agents = sorted(set(froms) | set(tos))
            return agents
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

    @router.delete("/inbox/{message_id}")
    async def delete_message(message_id: str, request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            msg = db.query(HubMessage).filter(HubMessage.id == message_id).first()
            if not msg:
                raise HTTPException(status_code=404, detail="Message not found")
            db.delete(msg)
            db.commit()
            return {"deleted": True, "id": message_id}
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

    @router.get("/canon/series")
    async def list_canon_series(request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            from sqlalchemy import select as sa_select
            names = db.execute(
                sa_select(HubCanon.series).distinct().order_by(HubCanon.series)
            ).scalars().all()
            return sorted(names)
        finally:
            db.close()

    @router.get("/canon/{series}")
    async def list_canon(series: str, request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            rows = (
                db.query(HubCanon)
                .filter(HubCanon.series.ilike(f"%{series}%"))
                .order_by(HubCanon.series, HubCanon.entity)
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

    # -----------------------------------------------------------------------
    # Project Sections
    # -----------------------------------------------------------------------

    @router.get("/projects/{name}/sections")
    async def list_project_sections(name: str, request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            proj = db.query(HubProject).filter(HubProject.name == name).first()
            if not proj:
                raise HTTPException(status_code=404, detail="Project not found")
            rows = (
                db.query(HubProjectSection)
                .filter(HubProjectSection.project_name == name)
                .order_by(HubProjectSection.section)
                .all()
            )
            return [
                {
                    "section": r.section,
                    "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                    "updated_by": r.updated_by,
                }
                for r in rows
            ]
        finally:
            db.close()

    @router.get("/projects/{name}/sections/{section}")
    async def get_project_section(name: str, section: str, request: Request):
        require_authenticated_request(request)
        if section == "recent_changes":
            db = SessionLocal()
            try:
                entries = (
                    db.query(HubProjectChangelog)
                    .filter(HubProjectChangelog.project_name == name)
                    .order_by(HubProjectChangelog.created_at.desc())
                    .limit(20)
                    .all()
                )
                lines = [
                    f"[{e.created_at.isoformat() if e.created_at else '?'}]"
                    f"{(' (' + e.created_by + ')') if e.created_by else ''} {e.entry}"
                    for e in entries
                ]
                content = "\n".join(lines) if lines else "(no changelog entries)"
                return {"section": "recent_changes", "content": content,
                        "updated_at": None, "updated_by": None}
            finally:
                db.close()
        db = SessionLocal()
        try:
            row = (
                db.query(HubProjectSection)
                .filter(HubProjectSection.project_name == name,
                        HubProjectSection.section == section)
                .first()
            )
            if not row:
                return {"section": section, "content": "", "updated_at": None, "updated_by": None}
            return {
                "section": row.section,
                "content": row.content,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                "updated_by": row.updated_by,
            }
        finally:
            db.close()

    @router.put("/projects/{name}/sections/{section}")
    async def update_project_section(name: str, section: str, body: SectionUpdate, request: Request):
        require_authenticated_request(request)
        if section == "recent_changes":
            raise HTTPException(status_code=400,
                                detail="recent_changes is auto-generated from changelog")
        db = SessionLocal()
        try:
            proj = db.query(HubProject).filter(HubProject.name == name).first()
            if not proj:
                raise HTTPException(status_code=404, detail="Project not found")
            now = _utcnow()
            row = (
                db.query(HubProjectSection)
                .filter(HubProjectSection.project_name == name,
                        HubProjectSection.section == section)
                .first()
            )
            if row:
                row.content = body.content
                row.updated_at = now
                row.updated_by = body.updated_by
            else:
                row = HubProjectSection(
                    project_name=name,
                    section=section,
                    content=body.content,
                    updated_at=now,
                    updated_by=body.updated_by,
                )
                db.add(row)
            db.commit()
            db.refresh(row)
            return {
                "section": row.section,
                "content": row.content,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                "updated_by": row.updated_by,
            }
        finally:
            db.close()

    # -----------------------------------------------------------------------
    # Project Changelog
    # -----------------------------------------------------------------------

    @router.post("/projects/{name}/changelog")
    async def append_changelog(name: str, body: ChangelogAppend, request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            now = _utcnow()
            entry = HubProjectChangelog(
                project_name=name,
                entry=body.entry,
                created_at=now,
                created_by=body.created_by,
            )
            db.add(entry)
            db.commit()
            count = (
                db.query(HubProjectChangelog)
                .filter(HubProjectChangelog.project_name == name)
                .count()
            )
            if count > 50:
                oldest_ids = [
                    r[0] for r in (
                        db.query(HubProjectChangelog.id)
                        .filter(HubProjectChangelog.project_name == name)
                        .order_by(HubProjectChangelog.created_at.asc())
                        .limit(count - 50)
                        .all()
                    )
                ]
                db.query(HubProjectChangelog).filter(
                    HubProjectChangelog.id.in_(oldest_ids)
                ).delete(synchronize_session=False)
                db.commit()
            db.refresh(entry)
            return {
                "id": entry.id,
                "project_name": entry.project_name,
                "entry": entry.entry,
                "created_at": entry.created_at.isoformat() if entry.created_at else None,
                "created_by": entry.created_by,
            }
        finally:
            db.close()

    @router.get("/projects/{name}/changelog")
    async def get_changelog(name: str, request: Request, limit: int = 10):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            entries = (
                db.query(HubProjectChangelog)
                .filter(HubProjectChangelog.project_name == name)
                .order_by(HubProjectChangelog.created_at.desc())
                .limit(max(1, min(limit, 100)))
                .all()
            )
            return [
                {
                    "id": e.id,
                    "project_name": e.project_name,
                    "entry": e.entry,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                    "created_by": e.created_by,
                }
                for e in entries
            ]
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
