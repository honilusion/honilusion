"""
hub_server.py

MCP server exposing Agent Hub inbox and project tools.
"""

import asyncio
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

server = Server("hub")

_initialized = False


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _short_id():
    return str(uuid.uuid4())[:8]


def _ensure_init():
    global _initialized
    if _initialized:
        return
    _initialized = True
    from core.database import init_db
    init_db()


def _text(s: str) -> list[TextContent]:
    return [TextContent(type="text", text=s)]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="inbox_send",
            description="Send a message from one agent to another in the Agent Hub inbox.",
            inputSchema={
                "type": "object",
                "properties": {
                    "from_agent": {"type": "string", "description": "Sender agent name"},
                    "to_agent": {"type": "string", "description": "Recipient agent name"},
                    "content": {"type": "string", "description": "Message content"},
                    "related_project_id": {"type": "string", "description": "Optional project ID to link"},
                },
                "required": ["from_agent", "to_agent", "content"],
            },
        ),
        Tool(
            name="inbox_check",
            description="Check inbox messages for an agent.",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent name to check messages for"},
                    "unread_only": {"type": "boolean", "description": "Only return unread messages (default true)"},
                },
                "required": ["agent_id"],
            },
        ),
        Tool(
            name="inbox_mark_read",
            description="Mark an inbox message as read.",
            inputSchema={
                "type": "object",
                "properties": {
                    "message_id": {"type": "string", "description": "Message ID to mark as read"},
                },
                "required": ["message_id"],
            },
        ),
        Tool(
            name="inbox_delete",
            description="Delete an inbox message by ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "message_id": {"type": "string", "description": "Message ID to delete"},
                },
                "required": ["message_id"],
            },
        ),
        Tool(
            name="project_update",
            description="Create or update an Agent Hub project.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Project name (unique key)"},
                    "status": {
                        "type": "string",
                        "enum": ["open", "in_progress", "blocked", "done"],
                        "description": "Project status",
                    },
                    "owner_agent": {"type": "string", "description": "Agent that owns this project"},
                    "notes": {"type": "string", "description": "Project notes"},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="project_list",
            description="List all Agent Hub projects.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="project_get",
            description="Get a specific Agent Hub project by name.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Project name"},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="project_get_section",
            description="Get the content of a specific section for a project.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Project name"},
                    "section": {"type": "string", "description": "Section name (stack, file_map, patterns, completed, in_progress, planned, recent_changes, known_issues, conventions, agents)"},
                },
                "required": ["name", "section"],
            },
        ),
        Tool(
            name="project_update_section",
            description="Overwrite a project section's content.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Project name"},
                    "section": {"type": "string", "description": "Section name"},
                    "content": {"type": "string", "description": "New content for the section"},
                },
                "required": ["name", "section", "content"],
            },
        ),
        Tool(
            name="project_log",
            description="Append an entry to a project's changelog (auto-timestamps).",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Project name"},
                    "entry": {"type": "string", "description": "Changelog entry text"},
                },
                "required": ["name", "entry"],
            },
        ),
        Tool(
            name="project_changelog",
            description="Get recent changelog entries for a project.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Project name"},
                    "limit": {"type": "integer", "description": "Max entries to return (default 10)"},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="canon_get",
            description="Get a canon fact for a specific entity within a series.",
            inputSchema={
                "type": "object",
                "properties": {
                    "series": {"type": "string", "description": "Canon series name (e.g. 'characters')"},
                    "entity": {"type": "string", "description": "Entity name within the series"},
                },
                "required": ["series", "entity"],
            },
        ),
        Tool(
            name="canon_set",
            description="Create or update a canon fact for an entity in a series.",
            inputSchema={
                "type": "object",
                "properties": {
                    "series": {"type": "string", "description": "Canon series name"},
                    "entity": {"type": "string", "description": "Entity name within the series"},
                    "fact": {"type": "string", "description": "The canon fact text"},
                    "source_note": {"type": "string", "description": "Optional source or attribution note"},
                },
                "required": ["series", "entity", "fact"],
            },
        ),
        Tool(
            name="canon_list",
            description="List all canon facts for a series.",
            inputSchema={
                "type": "object",
                "properties": {
                    "series": {"type": "string", "description": "Canon series name to list"},
                },
                "required": ["series"],
            },
        ),
        Tool(
            name="persona_list",
            description="List all Hub personas, optionally filtered by category.",
            inputSchema={
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Filter by category name"},
                },
            },
        ),
        Tool(
            name="persona_get",
            description="Get full details and content of a persona by ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "persona_id": {"type": "string", "description": "Persona ID"},
                },
                "required": ["persona_id"],
            },
        ),
        Tool(
            name="persona_create",
            description="Create a new Hub persona (starts as draft).",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Persona name"},
                    "content": {"type": "string", "description": "System prompt content"},
                    "category": {"type": "string", "description": "Optional category"},
                    "notes": {"type": "string", "description": "Optional notes"},
                },
                "required": ["name", "content"],
            },
        ),
        Tool(
            name="persona_update",
            description="Update a persona's name, content, category, or notes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "persona_id": {"type": "string", "description": "Persona ID"},
                    "name": {"type": "string", "description": "New name"},
                    "content": {"type": "string", "description": "New content"},
                    "category": {"type": "string", "description": "New category"},
                    "notes": {"type": "string", "description": "New notes"},
                },
                "required": ["persona_id"],
            },
        ),
        Tool(
            name="persona_delete",
            description="Delete a persona (unpublishes from Odysseus first if published).",
            inputSchema={
                "type": "object",
                "properties": {
                    "persona_id": {"type": "string", "description": "Persona ID"},
                },
                "required": ["persona_id"],
            },
        ),
        Tool(
            name="persona_publish",
            description="Publish a persona to Odysseus system prompt templates.",
            inputSchema={
                "type": "object",
                "properties": {
                    "persona_id": {"type": "string", "description": "Persona ID"},
                },
                "required": ["persona_id"],
            },
        ),
        Tool(
            name="persona_unpublish",
            description="Unpublish a persona from Odysseus templates (reverts to draft).",
            inputSchema={
                "type": "object",
                "properties": {
                    "persona_id": {"type": "string", "description": "Persona ID"},
                },
                "required": ["persona_id"],
            },
        ),
        Tool(
            name="persona_import",
            description="Import all existing Odysseus system prompt templates as published personas.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="persona_test",
            description="Test a persona by sending it a prompt and returning the AI response.",
            inputSchema={
                "type": "object",
                "properties": {
                    "persona_id": {"type": "string", "description": "Persona ID"},
                    "prompt": {"type": "string", "description": "Test prompt to send"},
                    "model": {"type": "string", "description": "Model to use (optional)"},
                },
                "required": ["persona_id", "prompt"],
            },
        ),
        Tool(
            name="persona_category_list",
            description="List all persona categories.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="hub_retrieve_full",
            description="Retrieve a full tool output previously parked in the Agent Hub cache by ref_id.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ref_id": {"type": "string", "description": "Cache ref_id, format tc-{8 hex chars}"},
                },
                "required": ["ref_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    _ensure_init()

    from core.database import SessionLocal, HubMessage, HubProject, HubCanon

    if name == "inbox_send":
        from_agent = arguments.get("from_agent", "").strip()
        to_agent = arguments.get("to_agent", "").strip()
        content = arguments.get("content", "").strip()
        related_project_id = arguments.get("related_project_id")
        if not from_agent or not to_agent or not content:
            return _text("Error: from_agent, to_agent, and content are required")
        db = SessionLocal()
        try:
            now = _utcnow()
            msg = HubMessage(
                id=_short_id(),
                from_agent=from_agent,
                to_agent=to_agent,
                content=content,
                related_project_id=related_project_id,
                created_at=now,
                updated_at=now,
            )
            db.add(msg)
            db.commit()
            return _text(f"Message sent (id: {msg.id}) from {from_agent} to {to_agent}")
        except Exception as e:
            return _text(f"Error sending message: {e}")
        finally:
            db.close()

    elif name == "inbox_check":
        agent_id = arguments.get("agent_id", "").strip()
        unread_only = arguments.get("unread_only", True)
        if not agent_id:
            return _text("Error: agent_id is required")
        db = SessionLocal()
        try:
            q = db.query(HubMessage).filter(HubMessage.to_agent == agent_id)
            if unread_only:
                q = q.filter(HubMessage.read_at.is_(None))
            msgs = q.order_by(HubMessage.created_at.desc()).limit(50).all()
            if not msgs:
                label = "unread " if unread_only else ""
                return _text(f"No {label}messages for {agent_id}")
            lines = [f"{'Unread' if unread_only else 'All'} messages for {agent_id} ({len(msgs)}):\n"]
            for m in msgs:
                ts = m.created_at.isoformat() if m.created_at else "?"
                lines.append(f"- [{m.id}] {ts} from {m.from_agent}: {m.content[:200]}")
            return _text("\n".join(lines))
        finally:
            db.close()

    elif name == "inbox_mark_read":
        message_id = arguments.get("message_id", "").strip()
        if not message_id:
            return _text("Error: message_id is required")
        db = SessionLocal()
        try:
            msg = db.query(HubMessage).filter(HubMessage.id == message_id).first()
            if not msg:
                return _text(f"Error: Message {message_id!r} not found")
            if not msg.read_at:
                msg.read_at = _utcnow()
                msg.updated_at = _utcnow()
                db.commit()
            return _text(f"Message {message_id} marked as read")
        finally:
            db.close()

    elif name == "inbox_delete":
        message_id = arguments.get("message_id", "").strip()
        if not message_id:
            return _text("Error: message_id is required")
        db = SessionLocal()
        try:
            msg = db.query(HubMessage).filter(HubMessage.id == message_id).first()
            if not msg:
                return _text(f"Error: Message {message_id!r} not found")
            db.delete(msg)
            db.commit()
            return _text(f"Message {message_id} deleted")
        finally:
            db.close()

    elif name == "project_update":
        pname = arguments.get("name", "").strip()
        if not pname:
            return _text("Error: name is required")
        status = arguments.get("status", "open")
        owner_agent = arguments.get("owner_agent")
        notes = arguments.get("notes")
        db = SessionLocal()
        try:
            now = _utcnow()
            proj = db.query(HubProject).filter(HubProject.name == pname).first()
            if proj:
                proj.status = status
                if owner_agent is not None:
                    proj.owner_agent = owner_agent
                if notes is not None:
                    proj.notes = notes
                proj.updated_at = now
                db.commit()
                return _text(f"Project '{pname}' updated: status={status}")
            proj = HubProject(
                id=_short_id(),
                name=pname,
                status=status,
                owner_agent=owner_agent,
                notes=notes,
                created_at=now,
                updated_at=now,
            )
            db.add(proj)
            db.commit()
            return _text(f"Project '{pname}' created: status={status}")
        except Exception as e:
            return _text(f"Error updating project: {e}")
        finally:
            db.close()

    elif name == "project_list":
        db = SessionLocal()
        try:
            projects = (
                db.query(HubProject)
                .order_by(HubProject.updated_at.desc())
                .all()
            )
            if not projects:
                return _text("No projects found")
            lines = [f"Projects ({len(projects)}):\n"]
            for p in projects:
                owner = f" [{p.owner_agent}]" if p.owner_agent else ""
                lines.append(f"- {p.name} — {p.status}{owner}")
            return _text("\n".join(lines))
        finally:
            db.close()

    elif name == "project_get":
        pname = arguments.get("name", "").strip()
        if not pname:
            return _text("Error: name is required")
        db = SessionLocal()
        try:
            from core.database import HubProjectSection
            proj = db.query(HubProject).filter(HubProject.name == pname).first()
            if not proj:
                return _text(f"Project '{pname}' not found")
            lines = [
                f"Project: {proj.name}",
                f"Status: {proj.status}",
                f"Owner: {proj.owner_agent or '(none)'}",
                f"Created: {proj.created_at.isoformat() if proj.created_at else '?'}",
                f"Updated: {proj.updated_at.isoformat() if proj.updated_at else '?'}",
            ]
            sections = (
                db.query(HubProjectSection)
                .filter(HubProjectSection.project_name == pname)
                .order_by(HubProjectSection.section)
                .all()
            )
            if sections:
                lines.append("Sections:")
                for s in sections:
                    ts = s.updated_at.isoformat() if s.updated_at else "?"
                    by = f" (by {s.updated_by})" if s.updated_by else ""
                    lines.append(f"  - {s.section}: updated {ts}{by}")
            else:
                lines.append("Sections: (none — use project_update_section to add context)")
            return _text("\n".join(lines))
        finally:
            db.close()

    elif name == "project_get_section":
        pname = arguments.get("name", "").strip()
        section = arguments.get("section", "").strip()
        if not pname or not section:
            return _text("Error: name and section are required")
        db = SessionLocal()
        try:
            from core.database import HubProjectSection, HubProjectChangelog
            if section == "recent_changes":
                entries = (
                    db.query(HubProjectChangelog)
                    .filter(HubProjectChangelog.project_name == pname)
                    .order_by(HubProjectChangelog.created_at.desc())
                    .limit(20)
                    .all()
                )
                if not entries:
                    return _text(f"No changelog entries for '{pname}'")
                lines = [f"Recent changes for '{pname}' ({len(entries)}):\n"]
                for e in entries:
                    ts = e.created_at.isoformat() if e.created_at else "?"
                    by = f" ({e.created_by})" if e.created_by else ""
                    lines.append(f"- [{ts}]{by} {e.entry}")
                return _text("\n".join(lines))
            row = (
                db.query(HubProjectSection)
                .filter(HubProjectSection.project_name == pname,
                        HubProjectSection.section == section)
                .first()
            )
            if not row:
                return _text(f"Section '{section}' is empty for project '{pname}'")
            ts = row.updated_at.isoformat() if row.updated_at else "?"
            by = f" (by {row.updated_by})" if row.updated_by else ""
            return _text(f"[{pname}/{section}] updated {ts}{by}\n\n{row.content}")
        finally:
            db.close()

    elif name == "project_update_section":
        pname = arguments.get("name", "").strip()
        section = arguments.get("section", "").strip()
        content = arguments.get("content", "")
        if not pname or not section:
            return _text("Error: name and section are required")
        if section == "recent_changes":
            return _text("Error: recent_changes is auto-generated from changelog — use project_log instead")
        db = SessionLocal()
        try:
            from core.database import HubProjectSection
            now = _utcnow()
            row = (
                db.query(HubProjectSection)
                .filter(HubProjectSection.project_name == pname,
                        HubProjectSection.section == section)
                .first()
            )
            if row:
                row.content = content
                row.updated_at = now
                row.updated_by = "claude-code-server"
            else:
                row = HubProjectSection(
                    project_name=pname,
                    section=section,
                    content=content,
                    updated_at=now,
                    updated_by="claude-code-server",
                )
                db.add(row)
            db.commit()
            return _text(f"Section '{section}' updated for project '{pname}'")
        except Exception as e:
            return _text(f"Error updating section: {e}")
        finally:
            db.close()

    elif name == "project_log":
        pname = arguments.get("name", "").strip()
        entry = arguments.get("entry", "").strip()
        if not pname or not entry:
            return _text("Error: name and entry are required")
        db = SessionLocal()
        try:
            from core.database import HubProjectChangelog
            now = _utcnow()
            log_entry = HubProjectChangelog(
                project_name=pname,
                entry=entry,
                created_at=now,
                created_by="claude-code-server",
            )
            db.add(log_entry)
            db.commit()
            count = (
                db.query(HubProjectChangelog)
                .filter(HubProjectChangelog.project_name == pname)
                .count()
            )
            if count > 50:
                oldest = [
                    r[0] for r in (
                        db.query(HubProjectChangelog.id)
                        .filter(HubProjectChangelog.project_name == pname)
                        .order_by(HubProjectChangelog.created_at.asc())
                        .limit(count - 50)
                        .all()
                    )
                ]
                db.query(HubProjectChangelog).filter(
                    HubProjectChangelog.id.in_(oldest)
                ).delete(synchronize_session=False)
                db.commit()
            return _text(f"Changelog entry added for project '{pname}'")
        except Exception as e:
            return _text(f"Error adding changelog entry: {e}")
        finally:
            db.close()

    elif name == "project_changelog":
        pname = arguments.get("name", "").strip()
        limit = max(1, min(int(arguments.get("limit", 10)), 100))
        if not pname:
            return _text("Error: name is required")
        db = SessionLocal()
        try:
            from core.database import HubProjectChangelog
            entries = (
                db.query(HubProjectChangelog)
                .filter(HubProjectChangelog.project_name == pname)
                .order_by(HubProjectChangelog.created_at.desc())
                .limit(limit)
                .all()
            )
            if not entries:
                return _text(f"No changelog entries for '{pname}'")
            lines = [f"Changelog for '{pname}' (last {len(entries)}):\n"]
            for e in entries:
                ts = e.created_at.isoformat() if e.created_at else "?"
                by = f" ({e.created_by})" if e.created_by else ""
                lines.append(f"- [{ts}]{by} {e.entry}")
            return _text("\n".join(lines))
        finally:
            db.close()

    elif name == "canon_get":
        series = arguments.get("series", "").strip()
        entity = arguments.get("entity", "").strip()
        if not series or not entity:
            return _text("Error: series and entity are required")
        db = SessionLocal()
        try:
            row = db.query(HubCanon).filter(HubCanon.series == series, HubCanon.entity == entity).first()
            if not row:
                return _text(f"No canon fact found for {series}/{entity}")
            lines = [
                f"Series: {row.series}",
                f"Entity: {row.entity}",
                f"Fact: {row.fact}",
            ]
            if row.source_note:
                lines.append(f"Source: {row.source_note}")
            return _text("\n".join(lines))
        finally:
            db.close()

    elif name == "canon_set":
        series = arguments.get("series", "").strip()
        entity = arguments.get("entity", "").strip()
        fact = arguments.get("fact", "").strip()
        source_note = arguments.get("source_note")
        if not series or not entity or not fact:
            return _text("Error: series, entity, and fact are required")
        db = SessionLocal()
        try:
            now = _utcnow()
            row = db.query(HubCanon).filter(HubCanon.series == series, HubCanon.entity == entity).first()
            if row:
                row.fact = fact
                if source_note is not None:
                    row.source_note = source_note
                row.updated_at = now
                db.commit()
                return _text(f"Canon fact updated for {series}/{entity}")
            row = HubCanon(
                id=_short_id(),
                series=series,
                entity=entity,
                fact=fact,
                source_note=source_note,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            db.commit()
            return _text(f"Canon fact created for {series}/{entity}")
        except Exception as e:
            return _text(f"Error setting canon fact: {e}")
        finally:
            db.close()

    elif name == "canon_list":
        series = arguments.get("series", "").strip()
        if not series:
            return _text("Error: series is required")
        db = SessionLocal()
        try:
            rows = (
                db.query(HubCanon)
                .filter(HubCanon.series == series)
                .order_by(HubCanon.entity)
                .all()
            )
            if not rows:
                return _text(f"No canon facts found for series '{series}'")
            lines = [f"Canon facts for '{series}' ({len(rows)}):\n"]
            for r in rows:
                src = f" [{r.source_note}]" if r.source_note else ""
                lines.append(f"- {r.entity}: {r.fact}{src}")
            return _text("\n".join(lines))
        finally:
            db.close()

    def _get_preset_manager():
        from src.preset_manager import PresetManager
        from src.constants import DATA_DIR
        return PresetManager(DATA_DIR)

    if name == "persona_list":
        from core.database import HubPersona
        category = arguments.get("category", "").strip() or None
        db = SessionLocal()
        try:
            q = db.query(HubPersona)
            if category:
                q = q.filter(HubPersona.category == category)
            personas = q.order_by(HubPersona.updated_at.desc()).all()
            if not personas:
                return _text("No personas found")
            lines = [f"Personas ({len(personas)}):\n"]
            for p in personas:
                cat = f" [{p.category}]" if p.category else ""
                lines.append(f"- [{p.id}] {p.name} — {p.status}{cat}")
            return _text("\n".join(lines))
        finally:
            db.close()

    elif name == "persona_get":
        from core.database import HubPersona
        persona_id = arguments.get("persona_id", "").strip()
        if not persona_id:
            return _text("Error: persona_id is required")
        db = SessionLocal()
        try:
            p = db.query(HubPersona).filter(HubPersona.id == persona_id).first()
            if not p:
                return _text(f"Persona '{persona_id}' not found")
            lines = [
                f"Persona: {p.name}",
                f"ID: {p.id}",
                f"Status: {p.status}",
                f"Category: {p.category or '(none)'}",
                f"Odysseus ID: {p.odysseus_prompt_id or '(not published)'}",
                f"Notes: {p.notes or '(none)'}",
                f"Updated: {p.updated_at.isoformat() if p.updated_at else '?'}",
                "",
                "--- Content ---",
                p.content or "(empty)",
            ]
            return _text("\n".join(lines))
        finally:
            db.close()

    elif name == "persona_create":
        from core.database import HubPersona
        pname = arguments.get("name", "").strip()
        content = arguments.get("content", "")
        category = arguments.get("category")
        notes = arguments.get("notes")
        if not pname:
            return _text("Error: name is required")
        db = SessionLocal()
        try:
            now = _utcnow()
            p = HubPersona(
                id=_short_id(),
                name=pname,
                content=content,
                category=category,
                notes=notes,
                status="draft",
                created_at=now,
                updated_at=now,
            )
            db.add(p)
            db.commit()
            return _text(f"Persona '{pname}' created (id: {p.id})")
        except Exception as e:
            return _text(f"Error creating persona: {e}")
        finally:
            db.close()

    elif name == "persona_update":
        from core.database import HubPersona
        persona_id = arguments.get("persona_id", "").strip()
        if not persona_id:
            return _text("Error: persona_id is required")
        db = SessionLocal()
        try:
            p = db.query(HubPersona).filter(HubPersona.id == persona_id).first()
            if not p:
                return _text(f"Persona '{persona_id}' not found")
            if "name" in arguments:
                p.name = arguments["name"]
            if "content" in arguments:
                p.content = arguments["content"]
            if "category" in arguments:
                p.category = arguments["category"] or None
            if "notes" in arguments:
                p.notes = arguments["notes"] or None
            p.updated_at = _utcnow()
            if p.status == "published" and p.odysseus_prompt_id:
                pm = _get_preset_manager()
                pm.save_user_template({
                    "id": p.odysseus_prompt_id,
                    "name": p.name,
                    "system_prompt": p.content,
                    "temperature": 1.0,
                    "max_tokens": 0,
                })
            db.commit()
            return _text(f"Persona '{p.name}' updated")
        except Exception as e:
            return _text(f"Error updating persona: {e}")
        finally:
            db.close()

    elif name == "persona_delete":
        from core.database import HubPersona
        persona_id = arguments.get("persona_id", "").strip()
        if not persona_id:
            return _text("Error: persona_id is required")
        db = SessionLocal()
        try:
            p = db.query(HubPersona).filter(HubPersona.id == persona_id).first()
            if not p:
                return _text(f"Persona '{persona_id}' not found")
            pname = p.name
            if p.odysseus_prompt_id:
                pm = _get_preset_manager()
                pm.delete_user_template(p.odysseus_prompt_id)
            db.delete(p)
            db.commit()
            return _text(f"Persona '{pname}' deleted")
        except Exception as e:
            return _text(f"Error deleting persona: {e}")
        finally:
            db.close()

    elif name == "persona_publish":
        from core.database import HubPersona
        persona_id = arguments.get("persona_id", "").strip()
        if not persona_id:
            return _text("Error: persona_id is required")
        db = SessionLocal()
        try:
            p = db.query(HubPersona).filter(HubPersona.id == persona_id).first()
            if not p:
                return _text(f"Persona '{persona_id}' not found")
            pm = _get_preset_manager()
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
            return _text(f"Persona '{p.name}' published (template id: {template_id})")
        except Exception as e:
            return _text(f"Error publishing persona: {e}")
        finally:
            db.close()

    elif name == "persona_unpublish":
        from core.database import HubPersona
        persona_id = arguments.get("persona_id", "").strip()
        if not persona_id:
            return _text("Error: persona_id is required")
        db = SessionLocal()
        try:
            p = db.query(HubPersona).filter(HubPersona.id == persona_id).first()
            if not p:
                return _text(f"Persona '{persona_id}' not found")
            if p.odysseus_prompt_id:
                pm = _get_preset_manager()
                pm.delete_user_template(p.odysseus_prompt_id)
            p.odysseus_prompt_id = None
            p.status = "draft"
            p.updated_at = _utcnow()
            db.commit()
            return _text(f"Persona '{p.name}' unpublished (reverted to draft)")
        except Exception as e:
            return _text(f"Error unpublishing persona: {e}")
        finally:
            db.close()

    elif name == "persona_import":
        from core.database import HubPersona
        pm = _get_preset_manager()
        templates = pm.get_user_templates()
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
                p = HubPersona(
                    id=_short_id(),
                    name=t["name"],
                    content=t.get("system_prompt", ""),
                    status="published",
                    odysseus_prompt_id=t["id"],
                    created_at=now,
                    updated_at=now,
                )
                db.add(p)
                imported += 1
            db.commit()
            return _text(f"Import complete: {imported} imported, {skipped} skipped")
        except Exception as e:
            return _text(f"Error importing personas: {e}")
        finally:
            db.close()

    elif name == "persona_test":
        from core.database import HubPersona
        persona_id = arguments.get("persona_id", "").strip()
        prompt = arguments.get("prompt", "").strip()
        model = arguments.get("model", "")
        if not persona_id or not prompt:
            return _text("Error: persona_id and prompt are required")
        db = SessionLocal()
        try:
            p = db.query(HubPersona).filter(HubPersona.id == persona_id).first()
            if not p:
                return _text(f"Persona '{persona_id}' not found")
            content = p.content
        finally:
            db.close()
        try:
            from src.ai_interaction import _resolve_model
            from src.llm_core import llm_call_async
            import asyncio
            messages = [
                {"role": "system", "content": content},
                {"role": "user", "content": prompt},
            ]
            url, mdl, headers = _resolve_model(model or "")
            result = asyncio.run(llm_call_async(url, mdl, messages, temperature=1.0, max_tokens=1000, headers=headers))
            return _text(f"Response:\n\n{result}")
        except Exception as e:
            return _text(f"Error testing persona: {e}")

    elif name == "persona_category_list":
        from core.database import HubPersonaCategory
        db = SessionLocal()
        try:
            cats = db.query(HubPersonaCategory).order_by(HubPersonaCategory.name).all()
            if not cats:
                return _text("No categories found")
            lines = [f"Categories ({len(cats)}):\n"]
            for c in cats:
                lines.append(f"- [{c.id}] {c.name}")
            return _text("\n".join(lines))
        finally:
            db.close()

    elif name == "hub_retrieve_full":
        ref_id = arguments.get("ref_id", "").strip()
        if not re.fullmatch(r"tc-[0-9a-f]{8}", ref_id):
            return _text("Error: unsupported: malformed ref_id (expected tc-{8 hex chars})")
        from core.database import HubToolOutputCache
        db = SessionLocal()
        try:
            row = db.query(HubToolOutputCache).filter(HubToolOutputCache.ref_id == ref_id).first()
            if not row:
                return _text("Error: unsupported: ref not found (may have expired after 14 days)")
            lines = [
                f"Tool: {row.tool_name or '(unknown)'}",
                f"Created: {row.created_at.isoformat() if row.created_at else '?'}",
                f"Truncated: {row.truncated}",
                "",
                row.payload,
            ]
            if row.truncated:
                lines.insert(3, "NOTE: original payload exceeded 1 MB and was truncated at cache time.")
            return _text("\n".join(lines))
        finally:
            db.close()

    return _text(f"Unknown tool: {name}")


async def run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
