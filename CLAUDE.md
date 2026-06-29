# CLAUDE.md — Honilusion Odysseus Fork

This is a personal fork of [pewdiepie-archdaemon/odysseus](https://github.com/pewdiepie-archdaemon/odysseus).
Fork repo: https://github.com/honilusion/honilusion
Working branch: `honilusion-main`
Upstream branch: tracked as `upstream/main` and `upstream/dev`

---

## Server Context

- **Server:** olusionserver (192.168.1.157)
- **User:** olusion
- **Odysseus install path:** `/home/olusion/odysseus`
- **Odysseus web UI:** http://192.168.1.157:7000
- **Docker compose:** `sudo docker compose` (containers were started as root — always use sudo)

### Restarting Odysseus
Normal restart fails with permission denied. Use the kill pattern:
```bash
sudo kill -9 $(sudo docker inspect --format '{{.State.Pid}}' 51ed8cf3d0b9_odysseus-odysseus-1)
sudo docker rm 51ed8cf3d0b9_odysseus-odysseus-1
sudo docker compose up -d
```
Container name may change after rm — check with `docker ps`.

### CRITICAL: Static files are baked into the Docker image
`static/` (JS, HTML, CSS) is NOT volume-mounted — it's copied into the image at build time.
Editing files on disk does NOT update what the container serves.

**Two options after changing static files:**

Option A — Hot-copy into running container (fast, no restart needed):
```bash
docker cp static/js/settings.js odysseus-odysseus-1:/app/static/js/settings.js
docker cp static/index.html odysseus-odysseus-1:/app/static/index.html
```

Option B — Full rebuild (required for backend Python changes):
```bash
docker compose build odysseus
# Then kill + rm + up -d as above since restart is permission denied
```

**Volume-mounted paths** (changes on disk ARE live immediately):
- `data/` — sessions, auth, personal docs, ChromaDB
- `odysseus/` subdirectory contents that are bind-mounted (check docker inspect for full list)

Always verify a change is live after editing:
```bash
curl -s http://localhost:7000/static/js/settings.js | grep -c "your_new_function"
```

---

## Codebase Map

### Backend (Python/FastAPI)
- **Entry point:** `odysseus/app.py` — FastAPI app, route registration
- **Auth middleware:** `odysseus/routes/auth_routes.py` — session cookie + Bearer token auth
- **Auth helpers:** `odysseus/src/auth_helpers.py`
- **API token model:** `odysseus/core/database.py` — `ApiToken` SQLAlchemy model already exists
- **API token admin routes:** `odysseus/routes/api_token_routes.py` — admin-facing, has `_normalize_scopes()`
- **Agent loop:** `odysseus/src/agent_loop.py`
  - **Line ~648:** `_MCP_KEYWORDS` frozenset — gates MCP tool schema injection for local models
  - API models (Gemini, Claude, GPT) get ALL MCP schemas unconditionally
  - Local models ONLY get MCP schemas if the user message contains a keyword from this set
- **MCP manager:** loads integration config at startup from stored integrations
- **Database:** SQLite via SQLAlchemy, session via `core.database.get_db_session()`

### Frontend
- **Main HTML:** `odysseus/static/index.html` — single page app, all tabs/cards defined here
- **Settings JS:** `odysseus/static/js/settings.js` — all settings tab logic
  - Account tab initializes lazily — `initAccount()` must be confirmed to call sub-inits
- Tab refresh pattern: AI tab calls `refreshAiModelEndpoints()` on each click via `initTabs()`. Any new Account sub-section needs a matching hook: expose a `_reloadXxx` variable from the IIFE, then add `if (tab === 'account' && typeof _reloadXxx === 'function') _reloadXxx();` in `initTabs()`
- `el('element-id')` is the local helper for `document.getElementById`
- **App JS:** `odysseus/static/js/app.js`

### Data / Config
- `odysseus/data/` — gitignored by upstream .gitignore (personal data, sessions, etc.)
- `odysseus/data/api_tokens.json` — stub file, not committed (gitignored)
- Sessions stored in `odysseus/data/sessions.json`
- Personal docs: `odysseus/data/personal_docs/` → `/app/data/personal_docs/` inside container

### Docker / Networking
- Odysseus Docker network gateway: `172.20.0.1`
- Fileprep Docker network gateway: `172.21.0.1`
- Odysseus session cookie name: `odysseus_session` (not `session`)
- Document creation endpoint: `POST /api/document` (singular)
- Personal docs upload endpoint: `POST /api/personal/upload` (field name: `files`, plural)

---

## Honilusion Customizations (what we changed)

### agent_loop.py — MCP Keywords patch
Added fileprep-related keywords to `_MCP_KEYWORDS` at line ~648:
```
"preprocess", "fileprep", "library", "pdf", "docx"
```
**This patch breaks on upstream merges** — Feature 2 (configurable MCP keywords) is intended to replace this hardcoded fix.

### feat: persistent API tokens (commit 9c9ed08)
Files changed:
- `odysseus/routes/auth_routes.py` — added `GET/POST/DELETE /api/auth/tokens` endpoints (user self-service, not admin)
- `odysseus/static/index.html` — added Personal API Tokens card after 2FA card in Account tab
- `odysseus/static/js/settings.js` — added `initAccountTokens()` IIFE at end of `initAccount()`

Token format: `ody_` prefix + `secrets.token_urlsafe(32)`
Tokens are bcrypt-hashed, stored in SQLite `ApiToken` table, shown to user exactly once.

---

## External Services Running Alongside Odysseus

| Service | Port | Purpose |
|---|---|---|
| fileprep API | 7111 | File preprocessing to LLM-ready Markdown |
| fileprep MCP | 7112 | MCP server exposing fileprep tools to Odysseus |
| SearXNG (Odysseus) | 8082 | Web search for Odysseus |
| SearXNG (standalone) | 8080 | Separate instance |
| ChromaDB | 8100 | Vector memory for Odysseus |
| ntfy | 8091 | Notifications |

### fileprep MCP tools
- `preprocess_file` — process file, return Markdown
- `preprocess_to_document` — process file, create Odysseus Document
- `preprocess_to_library` — process file, index into ChromaDB
- `preprocess_batch` — multiple files
- `list/get/delete_saved_files` — manage outputs

FastMCP DNS rebinding protection must be disabled:
`TransportSecuritySettings(enable_dns_rebinding_protection=False)`

---

## Planned Features (honilusion-main)

- [x] Persistent API tokens (self-service, Settings → Account)
- [ ] Configurable MCP keywords per integration (Feature 2)
  - Add `keywords` field and `always_inject` checkbox to MCP server config UI
  - Merge integration keywords into `_MCP_KEYWORDS` at runtime
  - Replaces the hardcoded `agent_loop.py` patch

---

## Git Workflow

```bash
# Check upstream for updates without touching your branch
git fetch upstream

# See what changed upstream vs your branch
git diff honilusion-main upstream/dev

# Cherry-pick a specific upstream commit
git cherry-pick <commit-hash>

# Push your changes
git push origin honilusion-main
```

Never work directly on `main` — that branch tracks upstream.
