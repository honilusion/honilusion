"""
hub_server.py

MCP server exposing Agent Hub inbox and project tools.
"""

import asyncio
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

    return _text(f"Unknown tool: {name}")


async def run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
