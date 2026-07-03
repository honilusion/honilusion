# Honilusion Odysseus — Agent Briefing

This is the `honilusion-main` branch of the Odysseus personal AI assistant fork.
Repo: `https://github.com/honilusion/honilusion`
Server: `olusionserver` (192.168.1.157), web UI at `http://192.168.1.157:7000`

See `CLAUDE.md` for full deployment instructions, file map, and patterns.

---

## Completed Features (honilusion-main additions over upstream)

| Feature | Key files | Commit(s) |
|---|---|---|
| Persistent API tokens (self-service, Settings → Account) | `routes/auth_routes.py`, `static/js/settings.js`, `static/index.html` | `9c9ed08`, `57f4423` |
| Configurable MCP keywords + always-inject per integration | `core/database.py`, `routes/mcp_routes.py`, `src/agent_loop.py`, `static/js/settings.js` | — |
| Auto fileprep processing for chat file uploads | `src/chat_handler.py`, `core/database.py`, `Dockerfile` | `7dbb4e7` |
| Hub FastMCP HTTP endpoint for claude.ai connector | `routes/hub/hub_mcp.py`, `app.py` | — |
| Agent Hub Phase 2 — canon store | `core/database.py`, `routes/hub/hub_routes.py`, `mcp_servers/hub_server.py`, `static/hub/` | `e0b0a2c` |
| Agent Hub Phase 3 — project context store | `core/database.py`, `routes/hub/hub_routes.py`, `mcp_servers/hub_server.py`, `routes/hub/hub_mcp.py`, `static/hub/` | `be626f7` |
| Claude Code usage meter in left sidebar | `routes/usage_routes.py`, `static/style.css`, `static/index.html` | — |
| Skills system refactor (index mode, enable/disable, pre-pass) | `src/agent_loop.py`, `static/js/settings.js` | `c123123` |
| Agent Hub Phase 4 — personas store | `core/database.py`, `routes/hub/hub_routes.py`, `routes/hub/hub_mcp.py`, `static/hub/` | `3fa770a` |
| **fix: Persona publishing bugs** (see below) | `static/js/presets.js`, `routes/hub/hub_mcp.py`, `app.py` | `f361acf`, `51a8761` |

---

## Persona Publishing Fix (2026-07-03)

Two bugs caused published Hub personas to not appear in the Prompt > Persona tab:

**Bug 1 — Frontend staleness** (`f361acf`)
`openCustomPresetModal()` in `static/js/presets.js` used a stale module-level `userTemplates` array
loaded once at page init. Fix: made the function `async` and added `await loadUserTemplates()` at
the top so the dropdown fetches fresh data from the server on every open.

**Bug 2 — MCP singleton bypass** (`51a8761`)
`_get_preset_manager()` in `routes/hub/hub_mcp.py` created a throw-away `PresetManager` instance
per call. Saves went to disk but bypassed `app.state.preset_manager` (the singleton), so
`GET /api/presets/templates` returned stale data until server restart. Fix: added
`init_preset_manager(pm)` injection function; `app.py` calls it at startup to share the singleton.

**Known soft spot:** `_get_preset_manager()` silently falls back to a fresh instance if
`init_preset_manager()` was never called (e.g. tests that import hub_mcp directly). Future
hardening: raise instead of silently falling back, or add an integration test that exercises
the MCP publish path end-to-end.

---

## Known Issues

- **Auto fileprep delivery**: fileprep preprocessing may silently fail — `.md` output is produced
  but model doesn't receive it. Root cause not yet confirmed. Suspects: `is_document_file()`
  returns False for `.md`; `_inside_upload_dir()` rejects `.fileprep/` subpath; `_fileprep_file()`
  fails silently at debug log level.

- **Docker restart**: `sudo docker compose restart` fails with permission denied. Use
  `kill -TERM 1` inside container + `docker start` (no sudo needed).

- **SIGHUP no-op**: `kill -HUP 1` does NOT reload Python modules in uvicorn 0.49 non-reload mode.
  Always use TERM + start for Python changes.

---

## Deployment Quick Reference

```bash
# Hot-copy a static file (no restart needed):
docker cp static/js/presets.js <container>:/app/static/js/presets.js

# Restart after Python changes:
docker exec <container> kill -TERM 1
docker start <container>

# Container name (changes after full rm+up):
docker ps --format "{{.Names}}" | grep odysseus
# Current: 16cb127928d1_odysseus-odysseus-1
```
