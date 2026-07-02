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

async function loadAgents() {
  try {
    const agents = await apiFetch('/inbox/agents');
    const sel = document.getElementById('inbox-agent-input');
    const current = sel.value;
    sel.innerHTML = '<option value="">Select agent...</option>' +
      agents.map(a => `<option value="${esc(a)}"${a === current ? ' selected' : ''}>${esc(a)}</option>`).join('');
    if (!current && agents.includes('claude-code-server')) {
      sel.value = 'claude-code-server';
      loadInbox();
    } else if (!current && agents.length > 0) {
      sel.value = agents[0];
      loadInbox();
    }
  } catch (e) {
    // silently ignore — agents endpoint may be empty
  }
}

async function loadInbox() {
  const agent = document.getElementById('inbox-agent-input').value;
  clearError('inbox-error');
  const listEl = document.getElementById('inbox-list');
  if (!agent) {
    listEl.innerHTML = '<div class="empty-state">Select an agent above.</div>';
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
      const dismissBtn = `<button class="btn btn-ghost" data-action="dismiss-msg" data-id="${esc(m.id)}">Done / Dismiss</button>`;
      return `
        <div class="msg-item${isUnread ? ' unread' : ''}" id="msg-${esc(m.id)}">
          <div>
            <div class="msg-meta">from <strong>${esc(m.from_agent)}</strong> · ${esc(ts)} · id: ${esc(m.id)}</div>
            <div class="msg-content">${esc(m.content)}</div>
            ${proj}
          </div>
          <div>${dismissBtn}</div>
        </div>`;
    }).join('');
  } catch (e) {
    showError('inbox-error', e.message);
    listEl.innerHTML = '';
  }
}

async function dismissMessage(msgId) {
  try {
    await apiFetch(`/inbox/${encodeURIComponent(msgId)}`, { method: 'DELETE' });
    loadInbox();
    loadAgents();
  } catch (e) {
    showError('inbox-error', 'Failed to dismiss: ' + e.message);
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
          <td style="white-space:nowrap">
            <button class="btn btn-ghost" data-action="save-project" data-name="${esc(p.name)}" data-id="${esc(p.id)}">Save</button>
            <button class="btn btn-ghost" style="margin-left:4px" data-action="open-context" data-name="${esc(p.name)}">Context</button>
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

// ---- Project Context ----

const VALID_SECTIONS = ['stack','file_map','patterns','completed','in_progress','planned','recent_changes','known_issues','conventions','agents'];
let _contextProject = null;

async function openProjectContext(name) {
  _contextProject = name;
  document.getElementById('ctx-project-name').textContent = name;
  document.getElementById('project-context-panel').classList.remove('hidden');
  clearError('ctx-error');
  try {
    const sections = await apiFetch(`/projects/${encodeURIComponent(name)}/sections`);
    renderContextTabs(sections);
  } catch (e) {
    showError('ctx-error', e.message);
  }
}

function closeProjectContext() {
  document.getElementById('project-context-panel').classList.add('hidden');
  _contextProject = null;
}

function renderContextTabs(sectionMeta) {
  const metaMap = {};
  sectionMeta.forEach(s => { metaMap[s.section] = s; });
  const tabsEl = document.getElementById('ctx-tabs');
  tabsEl.innerHTML = VALID_SECTIONS.map(s => {
    const has = metaMap[s];
    const label = s.replace(/_/g, ' ') + (has && s !== 'recent_changes' ? ' ✓' : '');
    return `<button class="ctx-tab" data-action="ctx-tab" data-section="${esc(s)}">${esc(label)}</button>`;
  }).join('');
  selectContextTab('stack');
}

async function selectContextTab(section) {
  document.querySelectorAll('.ctx-tab').forEach(b => {
    b.classList.toggle('ctx-tab-active', b.dataset.section === section);
  });
  const body = document.getElementById('ctx-body');
  clearError('ctx-error');
  body.innerHTML = '<div class="empty-state">Loading...</div>';
  try {
    if (section === 'recent_changes') {
      const entries = await apiFetch(`/projects/${encodeURIComponent(_contextProject)}/changelog?limit=20`);
      const listHtml = entries.length === 0
        ? '<div class="empty-state" style="padding:12px 0">No changelog entries yet.</div>'
        : entries.map(e => {
            const ts = e.created_at ? new Date(e.created_at + 'Z').toLocaleString() : '';
            const by = e.created_by ? ` · ${esc(e.created_by)}` : '';
            return `<div class="changelog-entry"><span class="changelog-meta">${esc(ts)}${by}</span><span class="changelog-text">${esc(e.entry)}</span></div>`;
          }).join('');
      body.innerHTML = `
        <div class="ctx-section-meta">Auto-generated from changelog — read only</div>
        <div id="changelog-list">${listHtml}</div>
        <div class="ctx-section-form">
          <label>Append changelog entry</label>
          <textarea id="ctx-changelog-entry" placeholder="What changed?" style="min-height:60px"></textarea>
          <div style="margin-top:8px"><button class="btn btn-primary" data-action="append-changelog">Add Entry</button></div>
        </div>`;
    } else {
      const data = await apiFetch(`/projects/${encodeURIComponent(_contextProject)}/sections/${encodeURIComponent(section)}`);
      const ts = data.updated_at ? new Date(data.updated_at + 'Z').toLocaleString() : null;
      const by = data.updated_by;
      const metaStr = ts ? `Last updated ${esc(ts)}${by ? ' by ' + esc(by) : ''}` : 'Not yet set';
      body.innerHTML = `
        <div class="ctx-section-meta">${metaStr}</div>
        <textarea id="ctx-section-content" style="min-height:200px;width:100%;font-family:monospace;font-size:12px">${esc(data.content || '')}</textarea>
        <div style="margin-top:8px"><button class="btn btn-primary" data-action="save-section" data-section="${esc(section)}">Save</button></div>`;
    }
  } catch (e) {
    showError('ctx-error', e.message);
    body.innerHTML = '';
  }
}

async function saveSection(section) {
  clearError('ctx-error');
  const content = document.getElementById('ctx-section-content').value;
  try {
    await apiFetch(`/projects/${encodeURIComponent(_contextProject)}/sections/${encodeURIComponent(section)}`, {
      method: 'PUT',
      body: JSON.stringify({ content, updated_by: null }),
    });
    selectContextTab(section);
  } catch (e) {
    showError('ctx-error', 'Save failed: ' + e.message);
  }
}

async function appendChangelog() {
  clearError('ctx-error');
  const entry = document.getElementById('ctx-changelog-entry').value.trim();
  if (!entry) { showError('ctx-error', 'Entry cannot be empty'); return; }
  try {
    await apiFetch(`/projects/${encodeURIComponent(_contextProject)}/changelog`, {
      method: 'POST',
      body: JSON.stringify({ entry, created_by: null }),
    });
    document.getElementById('ctx-changelog-entry').value = '';
    selectContextTab('recent_changes');
  } catch (e) {
    showError('ctx-error', 'Failed: ' + e.message);
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

async function loadSeriesList() {
  try {
    const series = await apiFetch('/canon/series');
    const sel = document.getElementById('canon-series-input');
    const current = sel.value;
    sel.innerHTML = '<option value="">Select series...</option>' +
      series.map(s => `<option value="${esc(s)}"${s === current ? ' selected' : ''}>${esc(s)}</option>`).join('') +
      '<option value="__new__">— Add new series —</option>';
    if (!current && series.includes('The Chaos Quotient')) {
      sel.value = 'The Chaos Quotient';
      loadCanon();
    } else if (!current && series.length > 0) {
      sel.value = series[0];
      loadCanon();
    }
  } catch (e) {
    // silently ignore
  }
}

async function loadCanon() {
  const sel = document.getElementById('canon-series-input');
  const selValue = sel.value;
  const newRow = document.getElementById('canon-new-series-row');

  if (selValue === '__new__') {
    newRow.classList.remove('hidden');
    return;
  }
  newRow.classList.add('hidden');

  const series = selValue === ''
    ? ''
    : (selValue === '__new__'
        ? document.getElementById('canon-new-series-input').value.trim()
        : selValue);

  clearError('canon-error');
  const tbody = document.getElementById('canon-body');
  if (!series) {
    tbody.innerHTML = '<tr><td colspan="4" class="empty-state" style="text-align:center;padding:12px">Select a series above.</td></tr>';
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
        <td><strong>${esc(r.entity)}</strong><br><small style="color:var(--text-dim)">${esc(r.series)}</small></td>
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
  const addSeries = document.getElementById('canon-add-series').value.trim();
  const entity = document.getElementById('canon-add-entity').value.trim();
  const fact = document.getElementById('canon-add-fact').value.trim();
  const src = document.getElementById('canon-add-src').value.trim();
  if (!addSeries || !entity || !fact) {
    showError('canon-add-error', 'Series, entity, and fact are required');
    return;
  }
  try {
    await apiFetch('/canon', {
      method: 'POST',
      body: JSON.stringify({ series: addSeries, entity, fact, source_note: src || null }),
    });
    document.getElementById('canon-add-entity').value = '';
    document.getElementById('canon-add-fact').value = '';
    document.getElementById('canon-add-src').value = '';
    _canonSeries = addSeries;
    await loadSeriesList();
    const sel = document.getElementById('canon-series-input');
    sel.value = addSeries;
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
  loadAgents();
  const agent = document.getElementById('inbox-agent-input').value;
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
document.getElementById('inbox-agent-input').addEventListener('change', loadInbox);
document.getElementById('send-msg-btn').addEventListener('click', sendMessage);
document.getElementById('add-project-btn').addEventListener('click', addProject);

// Event delegation for dynamically rendered inbox buttons
document.getElementById('inbox-list').addEventListener('click', function(e) {
  const btn = e.target.closest('[data-action="dismiss-msg"]');
  if (btn) dismissMessage(btn.dataset.id);
});

// Event delegation for project row buttons (save + context)
document.getElementById('projects-body').addEventListener('click', function(e) {
  const btn = e.target.closest('[data-action]');
  if (!btn) return;
  if (btn.dataset.action === 'save-project') saveProjectRow(btn.dataset.name, btn.dataset.id);
  if (btn.dataset.action === 'open-context') openProjectContext(btn.dataset.name);
});

// Event delegation for project context panel
document.getElementById('project-context-panel').addEventListener('click', function(e) {
  const btn = e.target.closest('[data-action]');
  if (!btn) return;
  if (btn.dataset.action === 'ctx-tab') selectContextTab(btn.dataset.section);
  if (btn.dataset.action === 'save-section') saveSection(btn.dataset.section);
  if (btn.dataset.action === 'append-changelog') appendChangelog();
  if (btn.dataset.action === 'close-context') closeProjectContext();
});

document.getElementById('load-canon-btn').addEventListener('click', loadCanon);
document.getElementById('canon-series-input').addEventListener('change', loadCanon);
document.getElementById('add-canon-btn').addEventListener('click', addCanon);

// Event delegation for canon table rows
document.getElementById('canon-body').addEventListener('click', function(e) {
  const btn = e.target.closest('[data-action]');
  if (!btn) return;
  if (btn.dataset.action === 'save-canon') saveCanonRow(btn.dataset.series, btn.dataset.entity, btn.dataset.id);
  if (btn.dataset.action === 'delete-canon') deleteCanonRow(btn.dataset.series, btn.dataset.entity);
});

// Init — restore saved token, populate dropdowns
(function init() {
  const saved = localStorage.getItem('hub_bearer_token');
  if (saved) {
    document.getElementById('token-input').value = saved;
  }
  loadProjects();
  loadAgents();
  loadSeriesList();
  startCountdown();
})();
