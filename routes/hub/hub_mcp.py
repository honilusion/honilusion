"""
Hub FastMCP HTTP server.

Exposed as an ASGI sub-app mounted at /api/hub/mcp in app.py.
Auth is handled by the Odysseus auth middleware (Bearer ody_... tokens)
before requests reach this app — no additional auth is needed here.

The session manager must be started via its run() context manager before
the first request arrives.  app.py's _lifespan function does this.

Endpoint for claude.ai custom connector: https://odysseus.olusion.net/api/hub/mcp/
(Starlette mounts require a trailing slash on the actual endpoint path.)
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings, TransportSecurityMiddleware

# Patch out Content-Type validation — some MCP clients omit it.
# Must be applied before the FastMCP instance is created.
TransportSecurityMiddleware._validate_content_type = lambda self, ct: True

_hub_mcp = FastMCP(
    "hub",
    instructions=(
        "Agent Hub for Odysseus. "
        "Use inbox_send to send messages between agents, "
        "inbox_check to read messages for an agent, "
        "inbox_mark_read to mark a message as read, "
        "project_update to create or update a project, "
        "project_list to list all projects, "
        "project_get to get a specific project by name."
    ),
    streamable_http_path="/",
    stateless_http=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _short_id():
    return str(uuid.uuid4())[:8]


@_hub_mcp.tool()
def inbox_send(from_agent: str, to_agent: str, content: str, related_project_id: Optional[str] = None) -> str:
    """Send a message from one agent to another in the Agent Hub inbox."""
    if not from_agent or not to_agent or not content:
        return "Error: from_agent, to_agent, and content are required"
    from core.database import SessionLocal, HubMessage
    db = SessionLocal()
    try:
        now = _utcnow()
        msg = HubMessage(
            id=_short_id(),
            from_agent=from_agent.strip(),
            to_agent=to_agent.strip(),
            content=content.strip(),
            related_project_id=related_project_id,
            created_at=now,
            updated_at=now,
        )
        db.add(msg)
        db.commit()
        return f"Message sent (id: {msg.id}) from {from_agent} to {to_agent}"
    except Exception as e:
        return f"Error sending message: {e}"
    finally:
        db.close()


@_hub_mcp.tool()
def inbox_check(agent_id: str, unread_only: bool = True) -> str:
    """Check inbox messages for an agent."""
    if not agent_id:
        return "Error: agent_id is required"
    from core.database import SessionLocal, HubMessage
    db = SessionLocal()
    try:
        q = db.query(HubMessage).filter(HubMessage.to_agent == agent_id)
        if unread_only:
            q = q.filter(HubMessage.read_at.is_(None))
        msgs = q.order_by(HubMessage.created_at.desc()).limit(50).all()
        if not msgs:
            label = "unread " if unread_only else ""
            return f"No {label}messages for {agent_id}"
        lines = [f"{'Unread' if unread_only else 'All'} messages for {agent_id} ({len(msgs)}):\n"]
        for m in msgs:
            ts = m.created_at.isoformat() if m.created_at else "?"
            lines.append(f"- [{m.id}] {ts} from {m.from_agent}: {m.content[:200]}")
        return "\n".join(lines)
    finally:
        db.close()


@_hub_mcp.tool()
def inbox_mark_read(message_id: str) -> str:
    """Mark an inbox message as read."""
    if not message_id:
        return "Error: message_id is required"
    from core.database import SessionLocal, HubMessage
    db = SessionLocal()
    try:
        msg = db.query(HubMessage).filter(HubMessage.id == message_id).first()
        if not msg:
            return f"Error: Message {message_id!r} not found"
        if not msg.read_at:
            msg.read_at = _utcnow()
            msg.updated_at = _utcnow()
            db.commit()
        return f"Message {message_id} marked as read"
    finally:
        db.close()


@_hub_mcp.tool()
def project_update(
    name: str,
    status: str = "open",
    owner_agent: Optional[str] = None,
    notes: Optional[str] = None,
) -> str:
    """Create or update an Agent Hub project."""
    if not name:
        return "Error: name is required"
    from core.database import SessionLocal, HubProject
    db = SessionLocal()
    try:
        now = _utcnow()
        proj = db.query(HubProject).filter(HubProject.name == name).first()
        if proj:
            proj.status = status
            if owner_agent is not None:
                proj.owner_agent = owner_agent
            if notes is not None:
                proj.notes = notes
            proj.updated_at = now
            db.commit()
            return f"Project '{name}' updated: status={status}"
        proj = HubProject(
            id=_short_id(),
            name=name,
            status=status,
            owner_agent=owner_agent,
            notes=notes,
            created_at=now,
            updated_at=now,
        )
        db.add(proj)
        db.commit()
        return f"Project '{name}' created: status={status}"
    except Exception as e:
        return f"Error updating project: {e}"
    finally:
        db.close()


@_hub_mcp.tool()
def project_list() -> str:
    """List all Agent Hub projects."""
    from core.database import SessionLocal, HubProject
    db = SessionLocal()
    try:
        projects = db.query(HubProject).order_by(HubProject.updated_at.desc()).all()
        if not projects:
            return "No projects found"
        lines = [f"Projects ({len(projects)}):\n"]
        for p in projects:
            owner = f" [{p.owner_agent}]" if p.owner_agent else ""
            lines.append(f"- {p.name} — {p.status}{owner}")
        return "\n".join(lines)
    finally:
        db.close()


@_hub_mcp.tool()
def project_get(name: str) -> str:
    """Get a specific Agent Hub project by name, including section names and last-updated times."""
    if not name:
        return "Error: name is required"
    from core.database import SessionLocal, HubProject, HubProjectSection
    db = SessionLocal()
    try:
        proj = db.query(HubProject).filter(HubProject.name == name).first()
        if not proj:
            return f"Project '{name}' not found"
        lines = [
            f"Project: {proj.name}",
            f"Status: {proj.status}",
            f"Owner: {proj.owner_agent or '(none)'}",
            f"Created: {proj.created_at.isoformat() if proj.created_at else '?'}",
            f"Updated: {proj.updated_at.isoformat() if proj.updated_at else '?'}",
        ]
        sections = (
            db.query(HubProjectSection)
            .filter(HubProjectSection.project_name == name)
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
        return "\n".join(lines)
    finally:
        db.close()


@_hub_mcp.tool()
def project_get_section(name: str, section: str) -> str:
    """Get the content of a specific section for a project."""
    if not name or not section:
        return "Error: name and section are required"
    from core.database import SessionLocal, HubProjectSection, HubProjectChangelog
    db = SessionLocal()
    try:
        if section == "recent_changes":
            entries = (
                db.query(HubProjectChangelog)
                .filter(HubProjectChangelog.project_name == name)
                .order_by(HubProjectChangelog.created_at.desc())
                .limit(20)
                .all()
            )
            if not entries:
                return f"No changelog entries for '{name}'"
            lines = [f"Recent changes for '{name}' ({len(entries)}):\n"]
            for e in entries:
                ts = e.created_at.isoformat() if e.created_at else "?"
                by = f" ({e.created_by})" if e.created_by else ""
                lines.append(f"- [{ts}]{by} {e.entry}")
            return "\n".join(lines)
        row = (
            db.query(HubProjectSection)
            .filter(HubProjectSection.project_name == name,
                    HubProjectSection.section == section)
            .first()
        )
        if not row:
            return f"Section '{section}' is empty for project '{name}'"
        ts = row.updated_at.isoformat() if row.updated_at else "?"
        by = f" (by {row.updated_by})" if row.updated_by else ""
        return f"[{name}/{section}] updated {ts}{by}\n\n{row.content}"
    finally:
        db.close()


@_hub_mcp.tool()
def project_update_section(name: str, section: str, content: str) -> str:
    """Overwrite a project section's content."""
    if not name or not section:
        return "Error: name and section are required"
    if section == "recent_changes":
        return "Error: recent_changes is auto-generated — use project_log instead"
    from core.database import SessionLocal, HubProjectSection
    from datetime import datetime, timezone
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        row = (
            db.query(HubProjectSection)
            .filter(HubProjectSection.project_name == name,
                    HubProjectSection.section == section)
            .first()
        )
        if row:
            row.content = content
            row.updated_at = now
            row.updated_by = "claude-web"
        else:
            row = HubProjectSection(
                project_name=name,
                section=section,
                content=content,
                updated_at=now,
                updated_by="claude-web",
            )
            db.add(row)
        db.commit()
        return f"Section '{section}' updated for project '{name}'"
    except Exception as e:
        return f"Error updating section: {e}"
    finally:
        db.close()


@_hub_mcp.tool()
def project_log(name: str, entry: str) -> str:
    """Append an entry to a project's changelog (auto-timestamps, auto-prunes at 50)."""
    if not name or not entry:
        return "Error: name and entry are required"
    from core.database import SessionLocal, HubProjectChangelog
    from datetime import datetime, timezone
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        log_entry = HubProjectChangelog(
            project_name=name,
            entry=entry,
            created_at=now,
            created_by="claude-web",
        )
        db.add(log_entry)
        db.commit()
        count = (
            db.query(HubProjectChangelog)
            .filter(HubProjectChangelog.project_name == name)
            .count()
        )
        if count > 50:
            oldest = [
                r[0] for r in (
                    db.query(HubProjectChangelog.id)
                    .filter(HubProjectChangelog.project_name == name)
                    .order_by(HubProjectChangelog.created_at.asc())
                    .limit(count - 50)
                    .all()
                )
            ]
            db.query(HubProjectChangelog).filter(
                HubProjectChangelog.id.in_(oldest)
            ).delete(synchronize_session=False)
            db.commit()
        return f"Changelog entry added for project '{name}'"
    except Exception as e:
        return f"Error adding changelog entry: {e}"
    finally:
        db.close()


@_hub_mcp.tool()
def project_changelog(name: str, limit: int = 10) -> str:
    """Get recent changelog entries for a project (newest first)."""
    if not name:
        return "Error: name is required"
    from core.database import SessionLocal, HubProjectChangelog
    db = SessionLocal()
    try:
        entries = (
            db.query(HubProjectChangelog)
            .filter(HubProjectChangelog.project_name == name)
            .order_by(HubProjectChangelog.created_at.desc())
            .limit(max(1, min(limit, 100)))
            .all()
        )
        if not entries:
            return f"No changelog entries for '{name}'"
        lines = [f"Changelog for '{name}' (last {len(entries)}):\n"]
        for e in entries:
            ts = e.created_at.isoformat() if e.created_at else "?"
            by = f" ({e.created_by})" if e.created_by else ""
            lines.append(f"- [{ts}]{by} {e.entry}")
        return "\n".join(lines)
    finally:
        db.close()


def get_hub_mcp_app():
    """Return the FastMCP ASGI sub-app and initialise the session manager.

    Must be called at module level in app.py (before _lifespan is invoked)
    so that _hub_mcp.session_manager is available to the lifespan function.
    """
    return _hub_mcp.streamable_http_app()
