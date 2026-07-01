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
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    _ensure_init()

    from core.database import SessionLocal, HubMessage, HubProject

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
            proj = db.query(HubProject).filter(HubProject.name == pname).first()
            if not proj:
                return _text(f"Project '{pname}' not found")
            lines = [
                f"Project: {proj.name}",
                f"Status: {proj.status}",
                f"Owner: {proj.owner_agent or '(none)'}",
                f"Notes: {proj.notes or '(none)'}",
                f"Created: {proj.created_at.isoformat() if proj.created_at else '?'}",
                f"Updated: {proj.updated_at.isoformat() if proj.updated_at else '?'}",
            ]
            return _text("\n".join(lines))
        finally:
            db.close()

    return _text(f"Unknown tool: {name}")


async def run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
