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

from core.database import SessionLocal, HubMessage, HubProject, HubCanon, HubProjectSection, HubProjectChangelog, HubPersona, HubPersonaCategory, HubToolOutputCache
from src.auth_helpers import require_authenticated_request, effective_user
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


class PersonaCreate(BaseModel):
    name: str
    content: str
    category: Optional[str] = None
    notes: Optional[str] = None


class PersonaUpdate(BaseModel):
    name: Optional[str] = None
    content: Optional[str] = None
    category: Optional[str] = None
    notes: Optional[str] = None


class CategoryCreate(BaseModel):
    name: str


class PersonaTest(BaseModel):
    prompt: str
    model: str = ""


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

    # -----------------------------------------------------------------------
    # Personas — categories MUST be registered before /{persona_id}
    # -----------------------------------------------------------------------

    @router.get("/personas/categories")
    async def list_persona_categories(request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            cats = db.query(HubPersonaCategory).order_by(HubPersonaCategory.name).all()
            return [_category_dict(c) for c in cats]
        finally:
            db.close()

    @router.post("/personas/categories")
    async def create_persona_category(body: CategoryCreate, request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            now = _utcnow()
            cat = HubPersonaCategory(id=_short_id(), name=body.name, created_at=now, updated_at=now)
            db.add(cat)
            db.commit()
            db.refresh(cat)
            return _category_dict(cat)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
        finally:
            db.close()

    @router.put("/personas/categories/{cat_id}")
    async def rename_persona_category(cat_id: str, body: CategoryCreate, request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            cat = db.query(HubPersonaCategory).filter(HubPersonaCategory.id == cat_id).first()
            if not cat:
                raise HTTPException(status_code=404, detail="Category not found")
            old_name = cat.name
            cat.name = body.name
            cat.updated_at = _utcnow()
            db.query(HubPersona).filter(HubPersona.category == old_name).update(
                {"category": body.name}, synchronize_session=False
            )
            db.commit()
            db.refresh(cat)
            return _category_dict(cat)
        finally:
            db.close()

    @router.delete("/personas/categories/{cat_id}")
    async def delete_persona_category(cat_id: str, request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            cat = db.query(HubPersonaCategory).filter(HubPersonaCategory.id == cat_id).first()
            if not cat:
                raise HTTPException(status_code=404, detail="Category not found")
            db.query(HubPersona).filter(HubPersona.category == cat.name).update(
                {"category": None}, synchronize_session=False
            )
            db.delete(cat)
            db.commit()
            return {"deleted": True}
        finally:
            db.close()

    @router.post("/personas/import")
    async def import_personas(request: Request):
        require_authenticated_request(request)
        preset_manager = request.app.state.preset_manager
        templates = preset_manager.get_user_templates()
        db = SessionLocal()
        try:
            imported = 0
            skipped = 0
            now = _utcnow()
            for t in templates:
                existing = db.query(HubPersona).filter(HubPersona.name == t["name"]).first()
                if existing:
                    skipped += 1
                    continue
                persona = HubPersona(
                    id=_short_id(),
                    name=t["name"],
                    content=t.get("system_prompt", ""),
                    status="published",
                    odysseus_prompt_id=t["id"],
                    created_at=now,
                    updated_at=now,
                )
                db.add(persona)
                imported += 1
            db.commit()
            return {"imported": imported, "skipped": skipped}
        finally:
            db.close()

    @router.get("/personas")
    async def list_personas(request: Request, category: Optional[str] = None):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            q = db.query(HubPersona)
            if category:
                q = q.filter(HubPersona.category == category)
            personas = q.order_by(HubPersona.updated_at.desc()).all()
            return [_persona_dict(p) for p in personas]
        finally:
            db.close()

    @router.post("/personas")
    async def create_persona(body: PersonaCreate, request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            now = _utcnow()
            persona = HubPersona(
                id=_short_id(),
                name=body.name,
                content=body.content,
                category=body.category,
                notes=body.notes,
                status="draft",
                created_at=now,
                updated_at=now,
            )
            db.add(persona)
            db.commit()
            db.refresh(persona)
            return _persona_dict(persona)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
        finally:
            db.close()

    @router.get("/personas/{persona_id}")
    async def get_persona(persona_id: str, request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            p = db.query(HubPersona).filter(HubPersona.id == persona_id).first()
            if not p:
                raise HTTPException(status_code=404, detail="Persona not found")
            return _persona_dict(p)
        finally:
            db.close()

    @router.put("/personas/{persona_id}")
    async def update_persona(persona_id: str, body: PersonaUpdate, request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            p = db.query(HubPersona).filter(HubPersona.id == persona_id).first()
            if not p:
                raise HTTPException(status_code=404, detail="Persona not found")
            if body.name is not None:
                p.name = body.name
            if body.content is not None:
                p.content = body.content
            if body.category is not None:
                p.category = body.category or None
            if body.notes is not None:
                p.notes = body.notes or None
            p.updated_at = _utcnow()
            if p.status == "published" and p.odysseus_prompt_id:
                pm = request.app.state.preset_manager
                pm.save_user_template({
                    "id": p.odysseus_prompt_id,
                    "name": p.name,
                    "system_prompt": p.content,
                    "temperature": 1.0,
                    "max_tokens": 0,
                })
            db.commit()
            db.refresh(p)
            return _persona_dict(p)
        finally:
            db.close()

    @router.delete("/personas/{persona_id}")
    async def delete_persona(persona_id: str, request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            p = db.query(HubPersona).filter(HubPersona.id == persona_id).first()
            if not p:
                raise HTTPException(status_code=404, detail="Persona not found")
            if p.odysseus_prompt_id:
                pm = request.app.state.preset_manager
                pm.delete_user_template(p.odysseus_prompt_id)
            db.delete(p)
            db.commit()
            return {"deleted": True}
        finally:
            db.close()

    @router.post("/personas/{persona_id}/publish")
    async def publish_persona(persona_id: str, request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            p = db.query(HubPersona).filter(HubPersona.id == persona_id).first()
            if not p:
                raise HTTPException(status_code=404, detail="Persona not found")
            pm = request.app.state.preset_manager
            template_id = p.odysseus_prompt_id or f"user-{p.id}"
            pm.save_user_template({
                "id": template_id,
                "name": p.name,
                "system_prompt": p.content,
                "temperature": 1.0,
                "max_tokens": 0,
            })
            p.odysseus_prompt_id = template_id
            p.status = "published"
            p.updated_at = _utcnow()
            db.commit()
            db.refresh(p)
            return _persona_dict(p)
        finally:
            db.close()

    @router.post("/personas/{persona_id}/unpublish")
    async def unpublish_persona(persona_id: str, request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            p = db.query(HubPersona).filter(HubPersona.id == persona_id).first()
            if not p:
                raise HTTPException(status_code=404, detail="Persona not found")
            if p.odysseus_prompt_id:
                pm = request.app.state.preset_manager
                pm.delete_user_template(p.odysseus_prompt_id)
            p.odysseus_prompt_id = None
            p.status = "draft"
            p.updated_at = _utcnow()
            db.commit()
            db.refresh(p)
            return _persona_dict(p)
        finally:
            db.close()

    @router.post("/personas/{persona_id}/test")
    async def test_persona(persona_id: str, body: PersonaTest, request: Request):
        require_authenticated_request(request)
        db = SessionLocal()
        try:
            p = db.query(HubPersona).filter(HubPersona.id == persona_id).first()
            if not p:
                raise HTTPException(status_code=404, detail="Persona not found")
            content = p.content
        finally:
            db.close()
        from src.ai_interaction import _resolve_model
        from src.llm_core import llm_call_async
        messages = [
            {"role": "system", "content": content},
            {"role": "user", "content": body.prompt},
        ]
        try:
            user = effective_user(request)
            url, model, headers = _resolve_model(body.model or "", owner=user)
            result = await llm_call_async(url, model, messages, temperature=1.0, max_tokens=1000, headers=headers)
            return {"response": result}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ---------------------------------------------------------------------------
    # Compression stats
    # ---------------------------------------------------------------------------

    @router.get("/compression_stats")
    async def compression_stats(request: Request):
        """Return token-savings stats derived from HubToolOutputCache.

        Tokens are approximated:
        - tokens_before: tiktoken cl100k_base on payload (the stored original text)
        - tokens_after: chars_after / 4 (the compressed marker's char count; marker
          text is not stored, only its length)
        Falls back to chars/4 for tokens_before if tiktoken is unavailable.

        Time windows: all_time, last_24h, last_7d (based on created_at).
        """
        require_authenticated_request(request)

        from datetime import timedelta
        from src.token_counter import count_tokens_approx, chars_to_tokens_approx

        db = SessionLocal()
        try:
            rows = db.query(HubToolOutputCache).filter(
                HubToolOutputCache.chars_before != None  # noqa: E711
            ).all()
        finally:
            db.close()

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cutoffs = {
            "all_time": None,
            "last_7d": now - timedelta(days=7),
            "last_24h": now - timedelta(hours=24),
        }

        def _window_stats(subset):
            if not subset:
                return {
                    "events": 0,
                    "chars_before": 0, "chars_after": 0,
                    "tokens_before_approx": 0, "tokens_after_approx": 0,
                    "reduction_pct": 0.0,
                }
            cb = sum(r.chars_before for r in subset)
            ca = sum(r.chars_after for r in subset)
            tb = sum(
                count_tokens_approx(r.payload) if r.payload else chars_to_tokens_approx(r.chars_before)
                for r in subset
            )
            ta = sum(chars_to_tokens_approx(r.chars_after) for r in subset)
            pct = round((1 - ta / tb) * 100, 1) if tb > 0 else 0.0
            return {
                "events": len(subset),
                "chars_before": cb, "chars_after": ca,
                "tokens_before_approx": tb, "tokens_after_approx": ta,
                "reduction_pct": pct,
            }

        windows = {}
        for label, cutoff in cutoffs.items():
            subset = rows if cutoff is None else [
                r for r in rows
                if r.created_at and r.created_at >= cutoff
            ]
            windows[label] = _window_stats(subset)

        # Per-tool breakdown (all time)
        by_tool: dict[str, list] = {}
        for r in rows:
            by_tool.setdefault(r.tool_name or "unknown", []).append(r)
        tool_breakdown = []
        for tool_name, tool_rows in sorted(by_tool.items()):
            s = _window_stats(tool_rows)
            s["tool_name"] = tool_name
            tool_breakdown.append(s)
        tool_breakdown.sort(key=lambda x: x["tokens_before_approx"], reverse=True)

        return {
            "token_counting": {
                "method": "tiktoken cl100k_base on payload for tokens_before; chars_after/4 ratio for tokens_after",
                "note": "cl100k_base approximates GPT-4-class tokenization; non-OpenAI models will differ",
            },
            **windows,
            "by_tool": tool_breakdown,
        }

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


def _persona_dict(p: HubPersona) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "content": p.content,
        "category": p.category,
        "status": p.status,
        "odysseus_prompt_id": p.odysseus_prompt_id,
        "notes": p.notes,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def _category_dict(c: HubPersonaCategory) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }
