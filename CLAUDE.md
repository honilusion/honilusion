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

## Planned Features (honilusion-main)

- [x] Persistent API tokens (self-service, Settings → Account)
- [x] Configurable MCP keywords per integration (Feature 2)
  - See commit: feat: configurable MCP keywords and always-inject per integration
- [x] Auto fileprep processing for chat file uploads (Feature 3)
  - See commit: feat: auto fileprep processing for chat file uploads + PyMuPDF in Dockerfile

### feat: auto fileprep processing for chat file uploads (commit 7dbb4e7)
Files changed:
- `core/database.py` — added `auto_fileprep` (Boolean, default False) column to `McpServer` model; added `_migrate_add_mcp_auto_fileprep_column()` and registered in `init_db()`
- `routes/mcp_routes.py` — `add_server()` accepts `auto_fileprep` Form param; `list_servers()` includes it in response; `toggle_server()` PATCH also accepts and saves it
- `static/js/settings.js` — in the edit form for an existing MCP server, shows "Auto fileprep uploads" toggle only when `srv.name === 'fileprep'`; "Save keywords" button also sends `auto_fileprep`
- `src/chat_handler.py` — added `_is_auto_fileprep_enabled()` (DB lookup), `_fileprep_file()` (async httpx POST to `http://172.21.0.1:7111/process`), and intercept in `preprocess_message()` between file resolution loop and attachment_meta loop; on success writes `<stem>_fileprepped.md` to `UPLOAD_DIR/.fileprep/` and updates `files_by_id` entry (path, name, mime) — then rest of the pipeline treats it as a text/markdown document via `_process_text_file()`
- `Dockerfile` — added `&& pip install --no-cache-dir pymupdf` to the requirements RUN step (was optional-only before)

**fileprep endpoint:** `POST http://172.21.0.1:7111/process` — field name `file`, returns plain Markdown. Supports: pdf, docx, xlsx, csv, md, txt, png, jpg, jpeg, tiff, bmp.

**Intercept point:** `src/chat_handler.py:preprocess_message()` — between the `resolve_upload()` loop and the `attachment_meta.append()` loop. The updated `files_by_id` dict flows through to the vision check and `build_user_content`, so prepped files skip image/vision paths and land in `_process_text_file()` (text/markdown mime + .md extension both satisfied).

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
