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

**Alternative for Python route changes (no sudo needed):** Hot-copy the updated `.py` into the container, then restart the container:
```bash
docker cp routes/auth_routes.py <container_name>:/app/routes/auth_routes.py
docker exec <container_name> kill -TERM 1   # graceful shutdown; Docker restarts with unless-stopped
# or: docker stop <container_name> && docker start <container_name>
```
**WARNING:** SIGHUP (`kill -HUP 1`) does NOT reload Python modules in uvicorn 0.49 (non-reload mode). It only works if the container has `--reload` in its startup command. For route signature changes, a container restart is required. `docker start <name>` re-uses the same container (preserving docker cp files) without needing sudo.

**Route function body changes only** (not signature changes) can sometimes be picked up after SIGHUP because Python may re-evaluate the function at call time — but this is unreliable. Always use `docker start` after `docker cp` for Python changes.

### CRITICAL: Static files are baked into the Docker image
`static/` (JS, HTML, CSS) is NOT volume-mounted — it's copied into the image at build time.
Editing files on disk does NOT update what the container serves.

**Two options after changing static files:**

Option A — Hot-copy into running container (fast, no restart needed, survives permission-denied restarts):
```bash
docker cp static/js/settings.js odysseus-odysseus-1:/app/static/js/settings.js
docker cp static/index.html odysseus-odysseus-1:/app/static/index.html
docker cp static/style.css odysseus-odysseus-1:/app/static/style.css
```

Option B — Full rebuild (required for backend Python changes, or to make hot-copies permanent):
```bash
docker compose build odysseus
# Then kill + rm + up -d as above since restart is permission denied
```

**Volume-mounted paths** (changes on disk ARE live immediately):
- `data/` → `/app/data` — sessions, auth, personal docs, ChromaDB
- `data/local/` → `/app/.local` — cookbook Python packages
- `data/huggingface/` → `/app/.cache/huggingface` — HF model cache
- `data/ssh/` → `/app/.ssh` — cookbook SSH keys
- `/home/olusion/odysseus-workspace` → `/workspace`

**Always verify a change is live after hot-copying:**
```bash
# Line count should match wc -l static/js/settings.js
curl -s http://localhost:7000/static/js/settings.js | wc -l
# Or grep for a function you just added
curl -s http://localhost:7000/static/js/settings.js | grep -c "your_new_function"
```

**The `_RevalidatingStatic` class** (`app.py` line ~436) sets `Cache-Control: no-cache` on all
`.js`, `.css`, and `.html` files served from `/static`. This forces the browser to revalidate on
every load, so a hard refresh is usually enough — but only once the container has the new bytes.

---

## Codebase Map

### Backend (Python/FastAPI)
- **Entry point:** `app.py` — FastAPI app, route registration, static file mounting
- **Auth router:** `routes/auth_routes.py` — prefix `/api/auth`, registered via `setup_auth_routes(auth_manager)` in `app.py:583-584`
  - `GET /api/auth/status` — current user info
  - `GET /api/auth/policy` — password policy
  - `POST /api/auth/change-password`
  - `GET/POST/DELETE /api/auth/tokens` — personal API token self-service (added in 9c9ed08)
  - `GET/POST /api/auth/2fa/*` — 2FA endpoints
  - `GET /api/auth/users` — admin only
- **Auth helpers:** `src/auth_helpers.py` — `get_current_user(request)`
- **API token model:** `core/database.py` — `ApiToken(TimestampMixin, Base)` SQLAlchemy model
  - `id` — `String`, primary key, 8-char UUID slice
  - `owner` — `String`, nullable, indexed (username)
  - `name` — `String`, not null (user-supplied label)
  - `token_hash` — `String`, bcrypt hash of raw token
  - `token_prefix` — `String`, first 8 chars of raw token (display only, never re-derive)
  - `scopes` — `String`, comma-separated (default `"chat"`)
  - `is_active` — `Boolean`, default `True`
  - `created_at` — `DateTime` via `TimestampMixin` (also adds `updated_at`)
  - `last_used_at` — `DateTime`, nullable, set by auth middleware on token auth
- **API token admin routes:** `routes/api_token_routes.py` — admin-facing CRUD, has `_normalize_scopes()`
- **Auth helpers:** `src/auth_helpers.py` — shared helpers; key functions:
  - `get_current_user(request)` — reads `request.state.current_user` (set by middleware); returns `"api"` for Bearer token callers, NOT the real owner
  - `effective_user(request)` — **use this for owner-scoped routes**; for Bearer tokens returns `request.state.api_token_owner` (the human who minted the token); for cookie sessions identical to `get_current_user`
  - `require_user(request)` — raises 403 if called by a Bearer token caller (API tokens must use scope-aware routes)
- **Bearer token middleware** (`app.py` ~line 361): validates `Authorization: Bearer ody_*` headers; on success sets `request.state.current_user = "api"`, `request.state.api_token = True`, `request.state.api_token_owner = <real_username>`, `request.state.api_token_scopes = [...]`. The `"api"` pseudo-user is intentional — keeps token callers out of cookie/session routes that check `request.state.current_user` directly.
- **Personal token endpoints must use `effective_user()`**: `GET/POST/DELETE /api/auth/tokens` in `routes/auth_routes.py` use `effective_user(request)` (not `_get_current_user()`). This is the only way those endpoints work with Bearer token auth. The local `_get_current_user()` helper inside that router reads only session cookies and always returns `None` for Bearer callers.
- **Agent loop:** `src/agent_loop.py`
  - **Line ~648:** `_MCP_KEYWORDS` frozenset — gates MCP tool schema injection for local models
  - API models (Gemini, Claude, GPT) get ALL MCP schemas unconditionally
  - Local models ONLY get MCP schemas if the user message contains a keyword from this set
- **MCP manager:** loads integration config at startup from stored integrations
- **Database:** SQLite via SQLAlchemy, session via `core.database.get_db_session()`
- **Constants:** `src/constants.py` — `BASE_DIR`, `STATIC_DIR` (used by app.py for static serving)

### Frontend
- **Main HTML:** `static/index.html` — single page app, all tabs/cards defined here
- **Settings JS:** `static/js/settings.js` — all settings tab logic
- **App JS:** `static/js/app.js`
- **UI utilities:** `static/js/ui.js` — exports `esc()`, modal helpers, etc.

#### settings.js key patterns

**Module-level helpers (top of file):**
```js
function el(id) { return document.getElementById(id); }      // getElementById shorthand
function esc(s) { return uiModule.esc(s); }                  // HTML-escape (delegates to ui.js)
```
`esc()` is the canonical HTML-escape for XSS prevention. Its implementation lives in
`static/js/ui.js:760` — `(s || '').replace(/[&<>"']/g, m => _ESC_MAP[m])`. Do NOT create
local copies; import via `uiModule.esc()`.

**Initialization flow:**
```
open(tab)              ← exported, called whenever settings modal opens
  └─ if (!initialized) initAll()
       ├─ modalEl = el('settings-modal')
       ├─ initTabs()          ← wires tab click handlers
       ├─ initialized = true  ← set BEFORE remaining inits; error in any init below
       │                        leaves initialized=true so initAll() never re-runs
       ├─ initDefaultChat(), initTeacherModel(), ...
       ├─ initAccount()       ← Account tab setup (one-time)
       └─ initIntegrations(), initEmailSettings(), ...
```

**ADMIN_TABS set** (`settings.js:25`):
```js
const ADMIN_TABS = new Set(['services', 'integrations', 'tools', 'users', 'system']);
```
Clicking any of these routes to `window.adminModule.open(tab)` instead of the normal
panel toggle. `account`, `ai`, `search`, `appearance`, `shortcuts`, `email`, `reminders`
are NOT admin tabs — they use the standard `classList.toggle('hidden', ...)` switch.

**Tab refresh hook pattern** — the AI tab calls `refreshAiModelEndpoints()` on each click.
Any new Account sub-section needs the same pattern:

1. Declare a module-level ref at the top of the file:
```js
let _reloadAccountTokens = null;   // add alongside initialized / modalEl / _authPolicy
```
2. Set it at the end of the IIFE that owns the loader:
```js
(function initAccountTokens() {
  // ... event listener setup ...
  async function loadMyTokens() { ... }
  _reloadAccountTokens = loadMyTokens;   // expose BEFORE initial call
  loadMyTokens();
})();
```
3. Wire it into `initTabs()` next to the AI hook:
```js
if (tab === 'ai') refreshAiModelEndpoints();
if (tab === 'account' && typeof _reloadAccountTokens === 'function') _reloadAccountTokens();
```

**Account panel HTML** (`static/index.html` line ~1921):
```
data-settings-panel="account"  (starts hidden)
  ├─ .admin-card            — user info / logout
  ├─ .admin-card            — Change Password
  ├─ .admin-card #settings-2fa-card     — 2FA (content populated by render2FA())
  └─ .admin-card #settings-api-tokens-card  — Personal API Tokens (added 9c9ed08)
```
The `settings-tok-list` / `settings-tok-form` / `settings-tok-reveal` elements live inside
the tokens card and are referenced by ID in `initAccountTokens()`.

**`syncAdminVisibility()`** — called on every `open()`. Hides/shows elements with the
`.admin-only` CSS class based on `window._isAdmin`. Cards that should be visible to ALL
users must NOT have `.admin-only`.

**Service worker** (`static/sw.js`):
- Cache name: `odysseus-v327` (bump when changing precache list or SW logic)
- JS/CSS: network-first, cache fallback
- HTML navigation: stale-while-revalidate
- `static/js/settings.js` is in the precache list — browsers that have SW installed will
  use cached JS if offline. Online requests always hit the network first.

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

### agent_loop.py — MCP Keywords patch (superseded by Feature 2)
Added fileprep-related keywords to `_MCP_KEYWORDS` at line ~648:
```
"preprocess", "fileprep", "library", "pdf", "docx"
```
These are now redundant (can be set per-server via UI) but kept as fallback. Feature 2 below replaces the need for hardcoded entries here.

### feat: persistent API tokens (commit 9c9ed08)
Files changed:
- `routes/auth_routes.py` — added `GET/POST/DELETE /api/auth/tokens` (user self-service, not admin)
- `static/index.html` — added Personal API Tokens card (`#settings-api-tokens-card`) after 2FA card in Account tab
- `static/js/settings.js` — added `initAccountTokens()` IIFE at end of `initAccount()`, plus `_reloadAccountTokens` hook (commit 57f4423)

Token format: `ody_` prefix + `secrets.token_urlsafe(32)`
Tokens are bcrypt-hashed, stored in SQLite `api_tokens` table (`ApiToken` model), shown to user exactly once on creation.

**Debugging lesson:** After the HTML/JS were committed, the card still didn't appear because the
Docker container was built before the commit and `static/` is baked into the image. Fixed by
`docker cp`-ing the updated files into the running container. See Docker section above.

### fix: token endpoints use effective_user for Bearer token compatibility (commit on honilusion-main)
Files changed:
- `routes/auth_routes.py` — added `from src.auth_helpers import effective_user` import; replaced `_get_current_user(request)` with `effective_user(request)` in `list_my_tokens`, `create_my_token`, and `revoke_my_token`

**Root cause:** `_get_current_user()` is a closure inside `setup_auth_routes()` that reads the session cookie directly. Bearer token callers have no cookie, so it always returned `None` → 401. The Bearer token middleware sets `request.state.current_user = "api"` (a pseudo-user) and stores the real owner on `request.state.api_token_owner`. `effective_user()` knows to read the owner field for Bearer callers.

**Deployment lesson (revised):** SIGHUP does NOT reload Python modules in uvicorn 0.49 (non-reload mode). Use `docker exec <container> kill -TERM 1` followed by `docker start <container>` to restart with updated Python code. The container retains `docker cp`'d files across restarts since it's the same container filesystem.

### feat: configurable MCP keywords and always-inject per integration
Files changed:
- `core/database.py` — added `keywords` (Text, nullable) and `always_inject` (Boolean, default False) columns to `McpServer` model; added `_migrate_add_mcp_keyword_columns()` and registered it in `init_db()`
- `routes/mcp_routes.py` — `add_server()` accepts `keywords` and `always_inject` Form params and saves them; `list_servers()` includes them in response; `toggle_server()` PATCH endpoint made `is_enabled` optional and also accepts `keywords`/`always_inject` for updating
- `src/agent_loop.py` — added `_load_mcp_server_inject_config()` function that reads enabled MCP servers from DB; updated local model MCP injection logic to: merge per-server keywords into effective keyword set, always inject tools from servers with `always_inject=True`, otherwise fall back to keyword matching
- `static/js/settings.js` — Add MCP Server form now has "Trigger keywords" text input and "Always inject" toggle; existing server management view shows and saves these fields via "Save keywords" button

**Schema:** `McpServer.keywords` is a comma-separated string (e.g. `"preprocess, pdf, fileprep"`), stored and parsed at agent-loop call time. `McpServer.always_inject` is a boolean; if true, the server's tools are always sent to local models regardless of keyword matching.

**Agent loop logic (local models only, ~line 2577 in agent_loop.py):**
- Load DB config once per round via `_load_mcp_server_inject_config()`
- `always_inject` servers are included unconditionally
- If any keyword in `_MCP_KEYWORDS ∪ {per-server keywords}` appears in the last user message, ALL MCP schemas are injected
- Otherwise only `always_inject` server schemas are sent
- API models (Gemini, Claude, GPT, etc.) are unaffected — they still get all schemas

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

## Agent Hub (Phase 1)

Added in commit "feat: Agent Hub Phase 1 — inbox and projects"

### Database tables (core/database.py)

**hub_projects**
- `id` — String PK (8-char UUID slice)
- `name` — String NOT NULL UNIQUE
- `status` — String NOT NULL DEFAULT 'open' (open/in_progress/blocked/done)
- `owner_agent` — String nullable
- `notes` — Text nullable
- `created_at`, `updated_at` — via TimestampMixin

**hub_messages**
- `id` — String PK (8-char UUID slice)
- `from_agent` — String NOT NULL
- `to_agent` — String NOT NULL
- `content` — Text NOT NULL
- `created_at`, `updated_at` — via TimestampMixin
- `read_at` — DateTime nullable
- `related_project_id` — String nullable FK → hub_projects.id

**hub_canon** (added Phase 2)
- `id` — String PK (8-char UUID slice)
- `series` — String NOT NULL (grouping key, e.g. "characters")
- `entity` — String NOT NULL (unique key within series)
- `fact` — Text NOT NULL
- `source_note` — String nullable
- `created_at`, `updated_at` — via TimestampMixin
- Unique constraint on `(series, entity)`

Migration: `_migrate_add_hub_tables()` + `_migrate_add_hub_canon_table()` registered in `init_db()` — uses PRAGMA/CREATE TABLE IF NOT EXISTS pattern.

### Routes (routes/hub/hub_routes.py)
Route prefix: `/api/hub/`
All endpoints require `require_authenticated_request()` from `src/auth_helpers.py`.

**Inbox:**
- `POST /api/hub/inbox` — send message `{from_agent, to_agent, content, related_project_id?}`
- `GET /api/hub/inbox/{agent_id}` — get messages for agent (unread first, limit 50)
- `PATCH /api/hub/inbox/{message_id}/read` — mark as read
- `GET /api/hub/inbox/{agent_id}/unread-count` — returns `{count: N}`

**Projects:**
- `GET /api/hub/projects` — list all projects
- `POST /api/hub/projects` — create or update project (upsert by name)
- `GET /api/hub/projects/{name}` — get project by name
- `PATCH /api/hub/projects/{name}` — update status/notes/owner_agent

**Canon (added Phase 2):**
- `GET /api/hub/canon/{series}` — list all facts for a series (ordered by entity)
- `GET /api/hub/canon/{series}/{entity}` — get single fact
- `POST /api/hub/canon` — upsert `{series, entity, fact, source_note?}`
- `DELETE /api/hub/canon/{series}/{entity}` — delete fact

Registered in app.py: `from routes.hub import setup_hub_routes` / `app.include_router(setup_hub_routes())`

### MCP server (mcp_servers/hub_server.py)
Built-in stdio MCP server registered as `"hub"` in `src/builtin_mcp.py`.
Directly accesses SQLite DB (same pattern as memory_server.py).

Tools:
- `inbox_send(from_agent, to_agent, content, related_project_id?)` — send message
- `inbox_check(agent_id, unread_only=True)` — list messages
- `inbox_mark_read(message_id)` — mark as read
- `project_update(name, status, owner_agent?, notes?)` — create/update project
- `project_list()` — list all projects
- `project_get(name)` — get project by name
- `canon_get(series, entity)` — get a canon fact
- `canon_set(series, entity, fact, source_note?)` — create/update a canon fact
- `canon_list(series)` — list all facts for a series

### MCP HTTP endpoint (routes/hub/hub_mcp.py)
FastMCP HTTP server exposing Hub tools for external MCP clients (e.g. claude.ai custom connectors).

**Source:** `routes/hub/hub_mcp.py` — FastMCP instance with all 6 hub tools.

**Mount:** `app.mount("/api/hub/mcp", _hub_mcp_asgi)` in `app.py` (after hub REST routes at line ~807).

**Lifespan:** `_hub_mcp_server.session_manager.run()` wrapped around `_startup_event`/`_shutdown_event` in `_lifespan` (app.py ~line 926). Required because the `StreamableHTTPSessionManager` uses an anyio task group that must be started before the first request.

**Endpoint URL (for claude.ai connector):** `https://odysseus.olusion.net/api/hub/mcp/`
(Starlette Mount semantics require a trailing slash — the canonical endpoint URL is with `/`.)

**Auth:** Odysseus Bearer token middleware handles `Authorization: Bearer ody_...` validation before the request reaches the FastMCP sub-app. Invalid tokens receive 401 from the middleware; unauthenticated requests receive 401. No additional auth logic is needed inside the FastMCP app.

**Transport:** FastMCP streamable HTTP, stateless mode (`stateless_http=True`) — each request is independent, no session state. DNS rebinding protection disabled (`TransportSecuritySettings(enable_dns_rebinding_protection=False)`). Content-Type validation patched out (`TransportSecurityMiddleware._validate_content_type = lambda self, ct: True`).

**Test:**
```bash
# Should return 406 "Client must accept text/event-stream" (valid MCP error — server is live)
curl -s --max-time 10 http://localhost:7000/api/hub/mcp/ \
  -H "Cookie: odysseus_session=<session-token>" \
  -H "Accept: application/json"

# Should return initialize result as SSE event
curl -s --max-time 10 -X POST http://localhost:7000/api/hub/mcp/ \
  -H "Cookie: odysseus_session=<session-token>" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
```

### Static GUI
Single-page dashboard at `/hub/` (served via `GET /hub/` route in app.py as FileResponse).
Source: `static/hub/index.html` — vanilla JS, dark theme matching Odysseus style.
Features: inbox viewer with unread highlighting, compose form, projects table with inline editing, canon section with series load/inline edit/delete/add form, 30-second auto-refresh.

Nav link: added `<button id="rail-hub">` in `static/index.html` icon rail (between Gallery and Library), opens `/hub/` in a new tab.

---

## Hub MCP Singleton Pattern

**Rule:** MCP tool functions in `routes/hub/hub_mcp.py` must use the app-level `PresetManager` singleton — never instantiate their own.

`_get_preset_manager()` had been creating a new `PresetManager(DATA_DIR)` per call. Saves went to disk but bypassed the in-memory singleton at `app.state.preset_manager`, so `GET /api/presets/templates` returned stale data (REST and MCP paths diverged). Fixed by adding `init_preset_manager(pm)` in `hub_mcp.py` and calling it from `app.py` after mounting the MCP sub-app. `_get_preset_manager()` now returns the injected singleton; new-instance fallback exists only for pre-init safety.

**Applies to any future shared state:** if a hub_mcp tool needs to read/write app state (memory manager, skills manager, etc.), inject via a similar `init_*` function — do not import the FastAPI `app` object into `hub_mcp.py` (circular import risk).

---

## Token Compression — Build Status

### Session 1: retrieve cache + hub_retrieve_full (complete)

Large tool outputs can be parked in a cache behind a short `ref_id` instead of
being re-injected into context, and fetched back later on demand. **Session 1
built the cache and the retrieval side only** — nothing yet writes to the
cache automatically; that's Session 2 (the compressor hooking
`format_tool_result` / `agent_loop.py`, out of scope this session).

**Model + migration:** `core/database.py` — `HubToolOutputCache` (`ref_id`
unique-indexed, `session_id`, `tool_name`, `payload`, `size_chars`,
`truncated`, `created_at` indexed) + `_migrate_add_hub_tool_output_cache()`,
registered in `init_db()`.

**Write helper (what Session 2 will call):** `core/tool_output_cache.py`
```python
def store_tool_output(payload: str, tool_name: str | None, session_id: str | None) -> str:
    """Runs the 14-day TTL sweep, applies the 1 MB cap, returns a ref_id
    (format tc-{8 hex chars})."""
```
Dependency-free; imports `core.database` lazily inside the function (house
convention — lets tests monkeypatch `core.database.SessionLocal`). Sweep is
sweep-on-write (no scheduler): every call first deletes cache rows older than
14 days. Payloads over 1 MB are stored truncated with `truncated=True`.

**Read side:** `hub_retrieve_full(ref_id)` — added to both
`mcp_servers/hub_server.py` (stdio) and `routes/hub/hub_mcp.py` (FastMCP HTTP,
auth via the existing Bearer middleware, no extra auth code needed). Validates
`tc-` + 8 hex chars, returns a loud `"unsupported: ..."` error string on a
malformed ref or a miss (expired/never-existed) — never an empty result.

**Known limitation — always-inject does NOT cover built-in MCP servers:**
The per-server `always_inject` flag (`McpServer.always_inject` in
`core/database.py`) only exists for DB-backed integrations added via
Settings → Integrations (e.g. `fileprep`). The five built-in stdio servers
(`image_gen`, `memory`, `rag`, `email`, `hub`) have **no** `mcp_servers` row —
they're registered purely in code by `register_builtin_servers()`
(`src/builtin_mcp.py`), which `app.py`'s startup runs immediately before
`mcp_manager.connect_all_enabled()` (the DB-driven connector). Inserting a
fake `McpServer` row with `id="hub"` to flip `always_inject` would make
`connect_all_enabled()` call `connect_server(server_id="hub")` a *second*
time — `_connect_stdio` in `src/mcp_manager.py` has no idempotency guard, so
this would spawn a duplicate `hub_server.py` subprocess and silently clobber
the first one's entries in `_sessions`/`_tools`/`_connections`. Not attempted.
`hub_retrieve_full` is still reachable without it: API models (Claude/GPT/
Gemini) get all MCP schemas unconditionally; local models get it whenever a
global `_MCP_KEYWORDS` hit occurs (the set includes the literal word "mcp").
Fixing this properly needs either a builtin-server-aware always-inject list in
`agent_loop.py`/`builtin_mcp.py`, or giving built-ins their own `McpServer`
rows with a `is_builtin` flag that `connect_all_enabled()` skips — both are
schema/agent_loop.py changes, out of scope for this session.

**Tests:** `tests/test_hub_tool_output_cache.py` — store/retrieve round-trip,
ref_id format, miss and malformed-ref loud errors, 1 MB truncation flag, TTL
sweep eviction. All passing; existing `hub`/`mcp` test suites unaffected
(138 passed after this change).

**Verified end-to-end** (2026-07-06) over the live FastMCP HTTP endpoint with
a real Bearer token: initialize succeeds, a stored ref returns its payload,
a bogus `tc-00000000` ref returns the loud not-found error, a malformed ref
returns the loud malformed error, and an unauthenticated request gets 401.
Test token and test cache row were deleted after verification.

### Session 1.5: discoverability gap-fix (complete, partial win — see below)

Session 1 flagged that `always_inject` can't be used for the built-in `hub`
server without risking a duplicate stdio subprocess. Session 1.5's brief was
to fix discoverability the safe way instead: **reverse the always-inject
decision and use keyword-gating** (the existing, working mechanism) by adding
a trigger keyword to `_MCP_KEYWORDS` (`agent_loop.py:669`) so a `tc-{8 hex}`
marker (the format `store_tool_output()` uses for ref_ids) causes
`hub_retrieve_full`'s schema to be injected for local models.

**First, a correction to Session 1's own record:** while tracing this, it
turned out `hub` was never excluded from the schema/prompt-injection path at
all. `McpManager.is_builtin(server_id)` (`src/mcp_manager.py:606-613`) — the
check that excludes built-ins from `get_all_openai_schemas()` and
`get_tool_descriptions_for_prompt()` — only covers `{"image_gen", "memory",
"rag", "email"}` plus anything prefixed `builtin_`. `"hub"` is **not** in
that set (it was added later, in Agent Hub Phase 1, and apparently never
back-added to `is_builtin()`). So `hub_retrieve_full` flows through the
*normal* dynamic MCP schema path exactly like a user-added integration —
confirmed by inspecting the deployed container directly. This doesn't change
Session 1's always-inject finding (that one is about `McpServer` DB rows,
a separate mechanism), but it means keyword-gating was always a live option
for `hub`, not a fallback for an otherwise-invisible tool.

**Key finding — `_extract_last_user_message` scope (this decided everything):**
`_extract_last_user_message()` (`agent_loop.py:770-778`) scans `messages` in
reverse for the first `role=="user"` entry and returns *only* that message's
text. It never looks at `role=="tool"` or `role=="assistant"` content. This
function feeds `_last_content`/`_effective_kw` matching at
`agent_loop.py:2704-2711`. Whether a `tc-` marker embedded in a tool result
is visible therefore depends entirely on `used_native`
(`_resolve_tool_blocks`, `agent_loop.py:1619-1659` — set per round based on
whether the model actually emitted native `tool_calls`):
- **Non-native / text branch** (`used_native=False`): `_append_tool_results`
  (`agent_loop.py:1727-1741`) wraps tool output in a single
  `untrusted_context_message(...)`, which is **`role: "user"`**
  (`src/prompt_security.py:60-82`). This becomes the last user-role message,
  so `_extract_last_user_message` returns it — a `tc-` marker inside **is**
  caught by keyword matching. **The `tc-` keyword fix works here.**
- **Native branch** (`used_native=True`): results become `role: "tool"`
  messages (`agent_loop.py:1720-1726`) — never scanned. A `tc-` marker here
  is **invisible** to keyword-gating, no matter what's in `_MCP_KEYWORDS`.

**This is not hypothetical for this deployment.** Live-tested against the
actual configured local endpoint (LM Studio, `172.20.0.1:5678`): given
`hub_retrieve_full`'s real schema, the model
(`gemma-4-e2b-it-the-deckard-heretic-uncensored-thinking@q4_k_s`) returned
`finish_reason: "tool_calls"` with a proper native `tool_calls` array — i.e.
**the real local model on this box uses the native path**, the one branch
the keyword fix does not cover. Feeding the real payload back (via the
genuine `hub_server.call_tool` dispatch) as a `role: "tool"` message, the
model correctly reported the retrieved content — proving the tool call
itself works end-to-end once the schema is present. What's unresolved is
getting the schema present on the turn after a compressor (Session 2) drops
a marker into a *native* tool result.

**What shipped:** `"tc-"` added to `_MCP_KEYWORDS`
(`src/agent_loop.py:669-680`, with an inline comment covering this same
scope note). `tests/test_agent_loop.py::TestHubRetrieveFullDiscoverability`
— four tests proving, against the real `_extract_last_user_message` /
`_append_tool_results` / `_MCP_KEYWORDS`: the keyword is registered; a
marker in a non-native tool result is discoverable; a marker in a native
tool result is **not** discoverable (documents the gap, doesn't just assume
it); a marker typed directly by a human always works. All passing; full
`hub`/`mcp`/`agent_loop` suite (200 tests) green after the change.

**Verdict — reporting, not improvising further:** the keyword fix is real,
safe, and strictly additive (API models were already covered; non-native
local models are now covered; nothing regresses). But for *this*
deployment's actual local model, it does not close the gap, because that
model uses native tool-calling. Closing it needs one of:
1. Widen the scan itself — e.g. have `_extract_last_user_message` (or a
   sibling used only for this keyword check) also inspect recent
   `role=="tool"` message content for a `tc-` marker.
2. A targeted fix in `_append_tool_results`'s native branch — e.g. detect a
   `tc-` marker in `tool_result_texts` at append time and stash a flag the
   schema-injection code checks next round, without touching the general
   keyword-matching function.
3. Accept the gap for now and have Session 2's compressor itself decide,
   per-call, whether to *also* emit a short natural-language hint in the
   marker text (not just `tc-xxxxxxxx` bare) that's more likely to surface
   through other means — doesn't fix discoverability, just papers over it.

None of these were implemented — each touches `agent_loop.py`'s matching
logic beyond "add a keyword," which needs a decision before Session 2 can
assume this path actually works.

---

## Planned Features (honilusion-main)

- [x] Persistent API tokens (self-service, Settings → Account)
- [x] Configurable MCP keywords per integration (Feature 2)
  - See commit: feat: configurable MCP keywords and always-inject per integration
- [x] Auto fileprep processing for chat file uploads (Feature 3)
  - See commit: feat: auto fileprep processing for chat file uploads + PyMuPDF in Dockerfile
- [x] Hub FastMCP HTTP endpoint for claude.ai connector (Feature 4)
  - See commit: feat: Hub FastMCP HTTP endpoint for claude.ai connector
- [x] Agent Hub Phase 2 — canon store
  - See commit: feat: Agent Hub Phase 2 — canon store
- [x] Agent Hub Phase 3 — project context store
  - See commit: feat: Agent Hub Phase 3 — project context store (sections + changelog)

### feat: Agent Hub Phase 3 — project context store (commit be626f7)
Files changed:
- `core/database.py` — added `HubProjectSection` model (Integer PK, project_name/section/content/updated_at/updated_by, UNIQUE on project_name+section) and `HubProjectChangelog` model (Integer PK, append-only); added `_migrate_add_hub_project_context_tables()` registered in `init_db()` — migrates existing project `notes` → `completed` section on first run
- `routes/hub/hub_routes.py` — added `SectionUpdate` and `ChangelogAppend` Pydantic models; 5 new REST endpoints under `/api/hub/projects/{name}/sections` and `/projects/{name}/changelog`; `recent_changes` section is virtual (generated from changelog); PUT rejects writes to `recent_changes`; changelog auto-prunes at 50 entries
- `mcp_servers/hub_server.py` — added `project_get_section`, `project_update_section`, `project_log`, `project_changelog` tools; updated `project_get` to return section names + last-updated timestamps (not content); MCP caller is identified as `"claude-code-server"` in `updated_by`/`created_by`
- `routes/hub/hub_mcp.py` — same 4 tools + `project_get` update for the FastMCP HTTP connector; caller identified as `"claude-web"`
- `static/hub/hub.js` — `VALID_SECTIONS` constant; `openProjectContext`, `closeProjectContext`, `renderContextTabs`, `selectContextTab`, `saveSection`, `appendChangelog`; Context button added to project rows; event delegation on `#project-context-panel` using data-action pattern
- `static/hub/index.html` — `#project-context-panel` div with `#ctx-tabs` and `#ctx-body`; CSS for `.ctx-tab`, `.ctx-tab-active`, `.changelog-entry`; projects table header renamed to "Actions" (width 140px)

Valid section names: `stack`, `file_map`, `patterns`, `completed`, `in_progress`, `planned`, `recent_changes`, `known_issues`, `conventions`, `agents`

`recent_changes` is read-only in the UI — auto-generated from changelog. The Hub dashboard shows it via the changelog GET endpoint. PUT to `recent_changes` returns 400.

### feat: Agent Hub Phase 2 — canon store (commit e0b0a2c)
Files changed:
- `core/database.py` — added `HubCanon` model (series/entity/fact/source_note, unique on series+entity); added `_migrate_add_hub_canon_table()` registered in `init_db()`
- `routes/hub/hub_routes.py` — added `CanonUpsert` model, 4 REST endpoints under `/api/hub/canon/`, `_canon_dict()` serializer; also corrected auth to `require_authenticated_request()` (not `require_user()`)
- `mcp_servers/hub_server.py` — added `canon_get`, `canon_set`, `canon_list` tools to list_tools() and call_tool()
- `static/hub/hub.js` — added `loadCanon()`, `saveCanonRow()`, `deleteCanonRow()`, `addCanon()` functions; event listeners on `#load-canon-btn`, `#add-canon-btn`, and delegated listeners on `#canon-body` using data-action pattern (no onclick attrs — CSP compliant)
- `static/hub/index.html` — added Canon section with series input + Load button, table (`#canon-body`) with entity/fact/source/actions columns, add-fact form (`#canon-add-*` fields, `#add-canon-btn`)

### feat: auto fileprep processing for chat file uploads (commit 7dbb4e7)
Files changed:
- `core/database.py` — added `auto_fileprep` (Boolean, default False) column to `McpServer` model; added `_migrate_add_mcp_auto_fileprep_column()` and registered in `init_db()`
- `routes/mcp_routes.py` — `add_server()` accepts `auto_fileprep` Form param; `list_servers()` includes it in response; `toggle_server()` PATCH also accepts and saves it
- `static/js/settings.js` — in the edit form for an existing MCP server, shows "Auto fileprep uploads" toggle only when `srv.name === 'fileprep'`; "Save keywords" button also sends `auto_fileprep`
- `src/chat_handler.py` — added `_is_auto_fileprep_enabled()` (DB lookup), `_fileprep_file()` (async httpx POST to `http://172.21.0.1:7111/process`), and intercept in `preprocess_message()` between file resolution loop and attachment_meta loop; on success writes `<stem>_fileprepped.md` to `UPLOAD_DIR/.fileprep/` and updates `files_by_id` entry (path, name, mime) — then rest of the pipeline treats it as a text/markdown document via `_process_text_file()`
- `Dockerfile` — added `&& pip install --no-cache-dir pymupdf` to the requirements RUN step (was optional-only before)

**fileprep endpoint:** `POST http://172.21.0.1:7111/process` — field name `file`, returns plain Markdown. Supports: pdf, docx, xlsx, csv, md, txt, png, jpg, jpeg, tiff, bmp.

**Intercept point:** `src/chat_handler.py:preprocess_message()` — between the `resolve_upload()` loop and the `attachment_meta.append()` loop. The updated `files_by_id` dict flows through to the vision check and `build_user_content`, so prepped files skip image/vision paths and land in `_process_text_file()` (text/markdown mime + .md extension both satisfied).

### Debugging auto fileprep delivery (session 2026-06-29)

**Symptom:** fileprep produces .md files in `.fileprep/` cache and `files_by_id` is updated, but the model does not receive the fileprepped content.

**Suspected failure points (to be confirmed by debug run):**
1. `upload_handler.is_document_file(display_name, mime)` in `build_user_content` (document_processor.py line ~471) may return False for `text/markdown` / `.md` files. If so, the else branch fires and delivers `[Attached non-text file]` to the model instead of calling `_process_text_file()`.
2. `upload_handler._inside_upload_dir(cache_path)` may reject the `.fileprep/` subpath if the handler does an exact prefix check that doesn't tolerate hidden subdirectories.
3. `_fileprep_file()` failure is logged at `debug` level (line 69 in chat_handler.py) — silent in production docker logs, so failures look like a no-op.

**Diagnostic logging added (NOT committed — diagnostic only):**
- `chat_handler.py`: `[DBG-FP]` lines log att_id, fi keys, path, ext, supported-ext check, fileprep call success/failure, cache_path, and the final `files_by_id` update.
- `document_processor.py`: `[DBG-BUC]` lines log upload_info dict, path/path_exists, `_inside_upload_dir` result, and `is_image/is_audio/is_document` flags for each attachment in `build_user_content`.

**Deployment:** `docker cp` both files + `docker exec <name> kill -TERM 1` + `docker start <name>` (no sudo needed). Container: `16cb127928d1_odysseus-odysseus-1` (check `docker ps` — name changes after full rm+up).

**To capture debug output:**
```bash
# In one terminal — stream logs filtered to debug lines:
docker logs -f 16cb127928d1_odysseus-odysseus-1 2>&1 | grep -E "\[DBG-(FP|BUC)\]"

# In another terminal — upload a PDF and send it in a chat:
SESSION_COOKIE="odysseus_session=<your-session-cookie>"
# Upload a test PDF:
curl -s -b "$SESSION_COOKIE" -F "file=@/tmp/test.pdf" http://localhost:7000/api/upload | jq .
# Then POST a chat message with the returned att_id:
curl -s -b "$SESSION_COOKIE" -X POST http://localhost:7000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"summarize this","session_id":"<sid>","attachment_ids":["<att_id>"]}'
```

**Fix path (once root cause confirmed):**
- If `is_document_file` returns False for `.md`: fix the upload_handler method (or add `.md` / `text/markdown` to its document mime/ext sets), OR change the fileprep intercept to call `_process_text_file()` directly and skip `build_user_content`'s branch check.
- If `_inside_upload_dir` rejects `.fileprep/`: write cache to a non-hidden subdir (e.g. `fileprep_cache/`) or patch the path check.

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
