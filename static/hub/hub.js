const API = '/api/hub';
let _countdown = 30;
let _countdownTimer = null;
let _refreshTimer = null;

function esc(s) {
  return String(s || '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
}

function getBearerToken() {
  return localStorage.getItem('hub_bearer_token') || '';
}

async function apiFetch(path, opts = {}) {
  const token = getBearerToken();
  const authHeader = token ? { 'Authorization': 'Bearer ' + token } : {};
  const r = await fetch(API + path, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...authHeader, ...(opts.headers || {}) },
    ...opts,
  });
  if (!r.ok) {
    if (r.status === 401) throw new Error('Auth failed — check Bearer token or log in');
    const txt = await r.text().catch(() => r.statusText);
    throw new Error(`HTTP ${r.status}: ${txt}`);
  }
  return r.json();
}

function saveToken() {
  const val = document.getElementById('token-input').value.trim();
  if (val) {
    localStorage.setItem('hub_bearer_token', val);
  } else {
    localStorage.removeItem('hub_bearer_token');
  }
  refresh();
  loadProjects();
}

function showError(id, msg) {
  const el = document.getElementById(id);
  el.textContent = msg;
  el.classList.remove('hidden');
}
function clearError(id) {
  document.getElementById(id).classList.add('hidden');
}

// ---- Inbox ----

async function loadInbox() {
  const agent = document.getElementById('inbox-agent-input').value.trim();
  clearError('inbox-error');
  const listEl = document.getElementById('inbox-list');
  if (!agent) {
    listEl.innerHTML = '<div class="empty-state">Enter an agent name.</div>';
    return;
  }
  try {
    const msgs = await apiFetch(`/inbox/${encodeURIComponent(agent)}`);
    const unread = msgs.filter(m => !m.read_at);
    const badge = document.getElementById('unread-badge');
    if (unread.length > 0) {
      badge.textContent = unread.length;
      badge.classList.remove('hidden');
    } else {
      badge.classList.add('hidden');
    }
    if (msgs.length === 0) {
      listEl.innerHTML = '<div class="empty-state">No messages.</div>';
      return;
    }
    listEl.innerHTML = msgs.map(m => {
      const isUnread = !m.read_at;
      const ts = m.created_at ? new Date(m.created_at + 'Z').toLocaleString() : '';
      const proj = m.related_project_id ? `<div class="msg-project">Project: ${esc(m.related_project_id)}</div>` : '';
      const readBtn = isUnread
        ? `<button class="btn btn-ghost" data-action="mark-read" data-id="${esc(m.id)}">Mark read</button>`
        : `<span style="color:var(--text-dim);font-size:11px">read</span>`;
      return `
        <div class="msg-item${isUnread ? ' unread' : ''}" id="msg-${esc(m.id)}">
          <div>
            <div class="msg-meta">from <strong>${esc(m.from_agent)}</strong> · ${esc(ts)} · id: ${esc(m.id)}</div>
            <div class="msg-content">${esc(m.content)}</div>
            ${proj}
          </div>
          <div>${readBtn}</div>
        </div>`;
    }).join('');
  } catch (e) {
    showError('inbox-error', e.message);
    listEl.innerHTML = '';
  }
}

async function markRead(msgId) {
  try {
    await apiFetch(`/inbox/${encodeURIComponent(msgId)}/read`, { method: 'PATCH' });
    loadInbox();
  } catch (e) {
    showError('inbox-error', 'Failed to mark read: ' + e.message);
  }
}

async function sendMessage() {
  clearError('compose-error');
  const from = document.getElementById('compose-from').value.trim();
  const to = document.getElementById('compose-to').value.trim();
  const content = document.getElementById('compose-content').value.trim();
  const proj = document.getElementById('compose-project-id').value.trim();
  if (!from || !to || !content) {
    showError('compose-error', 'from_agent, to_agent, and content are required');
    return;
  }
  try {
    await apiFetch('/inbox', {
      method: 'POST',
      body: JSON.stringify({ from_agent: from, to_agent: to, content, related_project_id: proj || null }),
    });
    document.getElementById('compose-content').value = '';
    document.getElementById('compose-project-id').value = '';
    // Reload inbox if showing the recipient
    const viewing = document.getElementById('inbox-agent-input').value.trim();
    if (viewing === to) loadInbox();
  } catch (e) {
    showError('compose-error', e.message);
  }
}

// ---- Projects ----

async function loadProjects() {
  clearError('projects-error');
  try {
    const projects = await apiFetch('/projects');
    const tbody = document.getElementById('projects-body');
    if (projects.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" class="empty-state" style="text-align:center;padding:20px">No projects yet.</td></tr>';
      return;
    }
    tbody.innerHTML = projects.map(p => {
      const statusClass = 'status-' + (p.status || 'open').replace(/[^a-z_]/g, '');
      return `
        <tr id="proj-row-${esc(p.id)}">
          <td><strong>${esc(p.name)}</strong><br><small style="color:var(--text-dim)">${esc(p.id)}</small></td>
          <td>
            <select id="proj-status-${esc(p.id)}">
              ${['open','in_progress','blocked','done'].map(s =>
                `<option value="${s}"${p.status===s?' selected':''}>${s}</option>`
              ).join('')}
            </select>
          </td>
          <td>
            <input id="proj-owner-${esc(p.id)}" value="${esc(p.owner_agent || '')}" placeholder="(none)" style="width:120px">
          </td>
          <td>
            <input id="proj-notes-${esc(p.id)}" value="${esc(p.notes || '')}" placeholder="—">
          </td>
          <td>
            <button class="btn btn-ghost" data-action="save-project" data-name="${esc(p.name)}" data-id="${esc(p.id)}">Save</button>
          </td>
        </tr>`;
    }).join('');
  } catch (e) {
    showError('projects-error', e.message);
  }
}

async function saveProjectRow(name, id) {
  clearError('projects-error');
  const status = document.getElementById('proj-status-' + id).value;
  const owner = document.getElementById('proj-owner-' + id).value.trim();
  const notes = document.getElementById('proj-notes-' + id).value.trim();
  try {
    await apiFetch(`/projects/${encodeURIComponent(name)}`, {
      method: 'PATCH',
      body: JSON.stringify({ status, owner_agent: owner || null, notes: notes || null }),
    });
    loadProjects();
  } catch (e) {
    showError('projects-error', 'Save failed: ' + e.message);
  }
}

async function addProject() {
  clearError('addproj-error');
  const name = document.getElementById('proj-name').value.trim();
  const status = document.getElementById('proj-status').value;
  const owner = document.getElementById('proj-owner').value.trim();
  const notes = document.getElementById('proj-notes').value.trim();
  if (!name) {
    showError('addproj-error', 'Project name is required');
    return;
  }
  try {
    await apiFetch('/projects', {
      method: 'POST',
      body: JSON.stringify({ name, status, owner_agent: owner || null, notes: notes || null }),
    });
    document.getElementById('proj-name').value = '';
    document.getElementById('proj-notes').value = '';
    document.getElementById('proj-owner').value = '';
    loadProjects();
  } catch (e) {
    showError('addproj-error', e.message);
  }
}

// ---- Canon ----

let _canonSeries = '';

async function loadCanon() {
  const series = document.getElementById('canon-series-input').value.trim();
  clearError('canon-error');
  const tbody = document.getElementById('canon-body');
  if (!series) {
    tbody.innerHTML = '<tr><td colspan="4" class="empty-state" style="text-align:center;padding:12px">Enter a series name and click Load.</td></tr>';
    return;
  }
  _canonSeries = series;
  try {
    const rows = await apiFetch(`/canon/${encodeURIComponent(series)}`);
    if (rows.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" class="empty-state" style="text-align:center;padding:12px">No facts in this series.</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(r => `
      <tr id="canon-row-${esc(r.id)}">
        <td><strong>${esc(r.entity)}</strong></td>
        <td>
          <textarea id="canon-fact-${esc(r.id)}" style="min-height:50px;width:100%">${esc(r.fact)}</textarea>
        </td>
        <td>
          <input id="canon-src-${esc(r.id)}" value="${esc(r.source_note || '')}" placeholder="(none)">
        </td>
        <td style="white-space:nowrap">
          <button class="btn btn-ghost" data-action="save-canon" data-series="${esc(r.series)}" data-entity="${esc(r.entity)}" data-id="${esc(r.id)}">Save</button>
          <button class="btn btn-ghost" style="color:var(--red);border-color:var(--red)" data-action="delete-canon" data-series="${esc(r.series)}" data-entity="${esc(r.entity)}" data-id="${esc(r.id)}">Del</button>
        </td>
      </tr>`).join('');
  } catch (e) {
    showError('canon-error', e.message);
    tbody.innerHTML = '';
  }
}

async function saveCanonRow(series, entity, id) {
  clearError('canon-error');
  const fact = document.getElementById('canon-fact-' + id).value.trim();
  const src = document.getElementById('canon-src-' + id).value.trim();
  if (!fact) { showError('canon-error', 'Fact cannot be empty'); return; }
  try {
    await apiFetch('/canon', {
      method: 'POST',
      body: JSON.stringify({ series, entity, fact, source_note: src || null }),
    });
    loadCanon();
  } catch (e) {
    showError('canon-error', 'Save failed: ' + e.message);
  }
}

async function deleteCanonRow(series, entity) {
  clearError('canon-error');
  try {
    await apiFetch(`/canon/${encodeURIComponent(series)}/${encodeURIComponent(entity)}`, { method: 'DELETE' });
    loadCanon();
  } catch (e) {
    showError('canon-error', 'Delete failed: ' + e.message);
  }
}

async function addCanon() {
  clearError('canon-add-error');
  const series = document.getElementById('canon-add-series').value.trim();
  const entity = document.getElementById('canon-add-entity').value.trim();
  const fact = document.getElementById('canon-add-fact').value.trim();
  const src = document.getElementById('canon-add-src').value.trim();
  if (!series || !entity || !fact) {
    showError('canon-add-error', 'Series, entity, and fact are required');
    return;
  }
  try {
    await apiFetch('/canon', {
      method: 'POST',
      body: JSON.stringify({ series, entity, fact, source_note: src || null }),
    });
    document.getElementById('canon-add-entity').value = '';
    document.getElementById('canon-add-fact').value = '';
    document.getElementById('canon-add-src').value = '';
    if (!document.getElementById('canon-series-input').value.trim()) {
      document.getElementById('canon-series-input').value = series;
    }
    _canonSeries = series;
    document.getElementById('canon-series-input').value = series;
    loadCanon();
  } catch (e) {
    showError('canon-add-error', e.message);
  }
}

// ---- Auto-refresh ----

function tick() {
  _countdown--;
  document.getElementById('countdown').textContent = _countdown;
  if (_countdown <= 0) {
    _countdown = 30;
    refresh();
  }
}

function refresh() {
  loadProjects();
  const agent = document.getElementById('inbox-agent-input').value.trim();
  if (agent) loadInbox();
}

function startCountdown() {
  if (_countdownTimer) clearInterval(_countdownTimer);
  _countdown = 30;
  _countdownTimer = setInterval(tick, 1000);
}

// ---- Event listeners ----

document.getElementById('token-save-btn').addEventListener('click', saveToken);
document.getElementById('load-inbox-btn').addEventListener('click', loadInbox);
document.getElementById('send-msg-btn').addEventListener('click', sendMessage);
document.getElementById('add-project-btn').addEventListener('click', addProject);

// Event delegation for dynamically rendered inbox buttons
document.getElementById('inbox-list').addEventListener('click', function(e) {
  const btn = e.target.closest('[data-action="mark-read"]');
  if (btn) markRead(btn.dataset.id);
});

// Event delegation for dynamically rendered project row save buttons
document.getElementById('projects-body').addEventListener('click', function(e) {
  const btn = e.target.closest('[data-action="save-project"]');
  if (btn) saveProjectRow(btn.dataset.name, btn.dataset.id);
});

document.getElementById('load-canon-btn').addEventListener('click', loadCanon);
document.getElementById('add-canon-btn').addEventListener('click', addCanon);

// Event delegation for canon table rows
document.getElementById('canon-body').addEventListener('click', function(e) {
  const btn = e.target.closest('[data-action]');
  if (!btn) return;
  if (btn.dataset.action === 'save-canon') saveCanonRow(btn.dataset.series, btn.dataset.entity, btn.dataset.id);
  if (btn.dataset.action === 'delete-canon') deleteCanonRow(btn.dataset.series, btn.dataset.entity);
});

// Init — restore saved token, auto-load inbox if token present
(function init() {
  const saved = localStorage.getItem('hub_bearer_token');
  if (saved) {
    document.getElementById('token-input').value = saved;
    document.getElementById('inbox-agent-input').value = 'claude-code-server';
    loadInbox();
  }
  loadProjects();
  startCountdown();
})();
