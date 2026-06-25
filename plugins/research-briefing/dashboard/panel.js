// panel.js — Hermes Research Briefing plugin
// All endpoints hit are public per the public_paths allowlist. We grab the
// session token from the SPA's index.html (which embeds it as
// window.__HERMES_SESSION_TOKEN__), then use it for any non-public calls.

const POLL_MS = 30000;
let _token = null;

async function getSessionToken() {
  if (_token) return _token;
  try {
    const r = await fetch('/', { cache: 'no-store' });
    if (!r.ok) return null;
    const html = await r.text();
    // The SPA injects: <script>window.__HERMES_SESSION_TOKEN__="...";</script>
    const m = html.match(/window\.__HERMES_SESSION_TOKEN__\s*=\s*"([^"]+)"/);
    if (m) {
      _token = m[1];
      window.__HERMES_SESSION_TOKEN__ = _token;
      return _token;
    }
  } catch (e) {
    console.error('token bootstrap failed:', e);
  }
  return null;
}

async function api(path) {
  const token = await getSessionToken();
  const headers = {};
  if (token) headers['X-Hermes-Session-Token'] = token;
  const r = await fetch(path, { headers, cache: 'no-store' });
  if (!r.ok) {
    return { error: `HTTP ${r.status}`, _status: r.status };
  }
  return r.json();
}

function setBadge(id, text, cls) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.className = 'badge ' + (cls || 'empty');
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function escapeHtml(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function fmtRel(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    const diff = Date.now() - d.getTime();
    const m = Math.floor(diff / 60000);
    if (m < 1) return 'just now';
    if (m < 60) return m + 'm ago';
    const h = Math.floor(m / 60);
    if (h < 24) return h + 'h ago';
    const days = Math.floor(h / 24);
    if (days < 30) return days + 'd ago';
    return d.toISOString().slice(0, 10);
  } catch { return iso; }
}

// ---------- Tabs ----------
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.add('hidden'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.remove('hidden');
  });
});

// ---------- BRIEFING ----------

async function loadBriefingTasks() {
  const el = document.getElementById('brief-tasks');
  try {
    const data = await api('/api/personal/snapshot');
    if (data.error) { el.innerHTML = `<div class="empty-state">⚠ ${escapeHtml(data.error)}</div>`; return; }
    const t = data.tasks || {};
    if (t.status === 'degraded') { el.innerHTML = `<div class="empty-state">⚠ degraded: ${escapeHtml(t.error || '')}</div>`; return; }
    const over = t.overdue || [];
    const due = t.due_24h || [];
    const open = (t.open || []).slice(0, 5);
    if (!over.length && !due.length && !open.length) { el.innerHTML = '<div class="empty-state">No open tasks. ✓</div>'; return; }
    const renderRow = (task, cls) => {
      const pr = task.priority || 3;
      return `<div class="row">
        <div class="title ${cls}">${escapeHtml(task.title)}</div>
        <div class="meta">
          <span class="priority-${pr}">P${pr}</span>
          <span>${task.due_at ? fmtRel(task.due_at) : 'no due'}</span>
          <span>id=${task.id}</span>
        </div>
      </div>`;
    };
    let html = '';
    if (over.length) html += `<div style="margin-bottom:8px"><strong style="color:#f85149">⚠ Overdue (${over.length}):</strong></div>` +
      over.slice(0, 3).map(t => renderRow(t, 'overdue')).join('');
    if (due.length) html += `<div style="margin-bottom:8px"><strong style="color:#d29922">⏰ Due 24h (${due.length}):</strong></div>` +
      due.slice(0, 3).map(t => renderRow(t, 'due-soon')).join('');
    if (open.length) html += `<div style="margin-bottom:8px"><strong>Open:</strong></div>` + open.map(t => renderRow(t, '')).join('');
    el.innerHTML = html;
  } catch (e) { el.innerHTML = `<div class="empty-state">⚠ ${escapeHtml(String(e))}</div>`; }
}

async function loadBriefingCalendar() {
  const el = document.getElementById('brief-cal');
  try {
    const data = await api('/api/personal/snapshot');
    if (data.error) { el.innerHTML = `<div class="empty-state">⚠ ${escapeHtml(data.error)}</div>`; return; }
    const c = data.calendar || {};
    if (c.status === 'no-scope') { el.innerHTML = '<div class="empty-state">no calendar scope</div>'; return; }
    if (c.status === 'degraded') { el.innerHTML = `<div class="empty-state">⚠ ${escapeHtml(c.error || '')}</div>`; return; }
    const now = Date.now();
    const horizon = now + 86400000;
    const events = (c.events || []).filter(e => {
      const t = e.start?.dateTime || e.start?.date;
      if (!t) return false;
      const ts = new Date(t).getTime();
      return ts >= now - 3600000 && ts <= horizon;
    });
    if (!events.length) { el.innerHTML = '<div class="empty-state">Nothing in the next 24h ✓</div>'; return; }
    el.innerHTML = events.slice(0, 5).map(e => {
      const s = e.start?.dateTime || e.start?.date || '';
      const time = s.includes('T') ?
        new Date(s).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' }) :
        s;
      return `<div class="row">
        <div class="title">${escapeHtml(e.summary || '(untitled)')}</div>
        <div class="meta">${escapeHtml(time)}${e.location ? ' · ' + escapeHtml(e.location) : ''}</div>
      </div>`;
    }).join('');
  } catch (e) { el.innerHTML = `<div class="empty-state">⚠ ${escapeHtml(String(e))}</div>`; }
}

async function loadBriefingMail() {
  const el = document.getElementById('brief-mail');
  try {
    const data = await api('/api/personal/snapshot');
    if (data.error) { el.innerHTML = `<div class="empty-state">⚠ ${escapeHtml(data.error)}</div>`; return; }
    const g = data.gmail || {};
    if (g.status === 'no-scope') { el.innerHTML = '<div class="empty-state">no gmail scope</div>'; return; }
    if (g.status === 'degraded') { el.innerHTML = `<div class="empty-state">⚠ ${escapeHtml(g.error || '')}</div>`; return; }
    const msgs = (g.messages || []).filter(m => (m.labels || []).includes('UNREAD'));
    if (!msgs.length) { el.innerHTML = '<div class="empty-state">No unread ✓</div>'; return; }
    el.innerHTML = msgs.slice(0, 3).map(m => `<div class="row">
      <div class="title">${escapeHtml(m.subject || '(no subject)')}</div>
      <div class="meta">${escapeHtml(m.from || '?')}</div>
    </div>`).join('');
  } catch (e) { el.innerHTML = `<div class="empty-state">⚠ ${escapeHtml(String(e))}</div>`; }
}

async function loadBriefingResearch() {
  const el = document.getElementById('brief-research');
  try {
    const data = await api('/api/research/projects?status=active');
    if (data.error) { el.innerHTML = `<div class="empty-state">⚠ ${escapeHtml(data.error)}</div>`; return; }
    const projs = data.projects || [];
    if (!projs.length) { el.innerHTML = '<div class="empty-state">No active projects ✓</div>'; return; }
    el.innerHTML = projs.slice(0, 5).map(p => {
      const conf = p.confidence_overall != null ? p.confidence_overall.toFixed(2) : '—';
      const openQ = Array.isArray(p.questions_open) ? p.questions_open.length : (p.questions_open || 0);
      return `<div class="row">
        <div class="title">${escapeHtml(p.title)}</div>
        <div class="meta">
          <span>conf <strong>${conf}</strong></span>
          <span>· Q${openQ}/${p.questions_total || 0} open</span>
          <span>· E${p.evidence_total || 0}</span>
          <span>· ${fmtRel(p.last_active)}</span>
        </div>
      </div>`;
    }).join('');
  } catch (e) { el.innerHTML = `<div class="empty-state">⚠ ${escapeHtml(String(e))}</div>`; }
}

async function loadBriefingWatchdog() {
  const el = document.getElementById('brief-watchdog');
  try {
    const data = await api('/api/watchdog/state');
    if (data._status === 500 || (data.error && /500/.test(data.error))) {
      // If the endpoint 500s (historical NameError bug — fixed 2026-06-21
      // but kept here as a safety net), fall back to /api/health/deep so
      // the panel still renders something useful.
      const d2 = await api('/api/health/deep');
      if (d2.probes && d2.probes.gateway) {
        const entries = Object.entries(d2.probes).map(([name, v]) => {
          const ok = v.ok ? '✓' : '⚠';
          const cls = v.ok ? '' : 'overdue';
          return `<div class="row">
            <div class="title ${cls}">${ok} ${escapeHtml(name)}</div>
            <div class="meta">${escapeHtml(v.msg || '')} ${v.ms ? '· ' + v.ms + 'ms' : ''}</div>
          </div>`;
        }).join('');
        el.innerHTML = entries + '<div class="empty-state" style="margin-top:8px;font-size:11px">(watchdog /state 500s — showing health/deep fallback)</div>';
        return;
      }
      el.innerHTML = '<div class="empty-state">⚠ watchdog endpoint unavailable</div>';
      return;
    }
    const svc = data.services || {};
    const rows = Object.entries(svc).map(([name, v]) => {
      const ok = v.ok ? '✓' : '⚠';
      const cls = v.ok ? '' : 'overdue';
      const fails = v.consecutive_failures ? ' · ' + v.consecutive_failures + ' fail' : '';
      return `<div class="row">
        <div class="title ${cls}">${ok} ${escapeHtml(name)}</div>
        <div class="meta">${escapeHtml(v.msg || '')}${fails}</div>
      </div>`;
    }).join('');
    el.innerHTML = rows || '<div class="empty-state">no services</div>';
  } catch (e) { el.innerHTML = `<div class="empty-state">⚠ ${escapeHtml(String(e))}</div>`; }
}

async function loadBriefingHealth() {
  const el = document.getElementById('brief-health');
  try {
    const data = await api('/api/health/deep');
    if (data.error) { el.innerHTML = `<div class="empty-state">⚠ ${escapeHtml(data.error)}</div>`; return; }
    const probes = data.probes || {};
    const probeList = Array.isArray(probes) ? probes : Object.entries(probes).map(([name, v]) => ({ name, ...v }));
    el.innerHTML = probeList.slice(0, 10).map(p => {
      const ok = p.ok ? '✓' : '⚠';
      const cls = p.ok ? '' : 'overdue';
      return `<div class="row">
        <div class="title ${cls}">${ok} ${escapeHtml(p.name || '?')}</div>
        <div class="meta">${escapeHtml(p.msg || '')} ${p.ms ? '· ' + p.ms + 'ms' : ''}</div>
      </div>`;
    }).join('') || '<div class="empty-state">no probes</div>';
  } catch (e) { el.innerHTML = `<div class="empty-state">⚠ ${escapeHtml(String(e))}</div>`; }
}

async function loadBriefing() {
  setText('last-update', new Date().toLocaleTimeString());
  await Promise.all([
    loadBriefingTasks(), loadBriefingCalendar(), loadBriefingMail(),
    loadBriefingResearch(), loadBriefingWatchdog(), loadBriefingHealth(),
  ]);
}

// ---------- SYNTHESES ----------

let _synthProjects = [];

async function loadSynthRecent() {
  const el = document.getElementById('synth-recent');
  try {
    const data = await api('/api/research/projects');
    if (data.error) { el.innerHTML = `<div class="empty-state">⚠ ${escapeHtml(data.error)}</div>`; return; }
    const projs = data.projects || [];
    _synthProjects = projs;
    const sel = document.getElementById('synth-slug');
    if (sel && sel.options.length === 0) {
      projs.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.slug;
        opt.textContent = p.slug + ' — ' + (p.title || '').slice(0, 50);
        sel.appendChild(opt);
      });
    }
    // Find recent synthesis events in timelines
    const synths = [];
    for (const p of projs) {
      const tl = p.timeline_tail || [];
      for (const ev of tl) {
        if (ev.event && ev.event.startsWith('synthesized answer')) {
          // Extract the question from the event text
          const m = ev.event.match(/to:\s*(.+?)\s*\(/);
          const q = m ? m[1] : ev.event;
          synths.push({ slug: p.slug, question: q, timestamp: ev.timestamp, event: ev.event });
        }
      }
    }
    synths.sort((a, b) => (b.timestamp || '').localeCompare(a.timestamp || ''));
    if (!synths.length) {
      el.innerHTML = '<div class="empty-state">No syntheses yet. Use the form below to make one.</div>';
      return;
    }
    el.innerHTML = synths.slice(0, 8).map(s => `<div class="synth-ev">
      <div class="head">
        <span class="id">${escapeHtml(s.slug)}</span>
        <span>${fmtRel(s.timestamp)}</span>
      </div>
      <div class="claim">${escapeHtml(s.question)}</div>
      <div class="srcs" style="margin-top:6px">
        <button onclick="relSynth('${escapeHtml(s.slug)}', '${escapeHtml(s.question.replace(/'/g, "\\'"))}')">re-run →</button>
      </div>
    </div>`).join('');
  } catch (e) { el.innerHTML = `<div class="empty-state">⚠ ${escapeHtml(String(e))}</div>`; }
}

function relSynth(slug, q) {
  document.getElementById('synth-slug').value = slug;
  document.getElementById('synth-q').value = q;
  runSynth();
}

async function runSynth() {
  const slug = document.getElementById('synth-slug').value;
  const q = document.getElementById('synth-q').value.trim();
  const out = document.getElementById('synth-result');
  if (!slug) { out.innerHTML = '<div class="ts">Pick a project.</div>'; return; }
  if (!q) { out.innerHTML = '<div class="ts">Enter a question.</div>'; return; }
  out.innerHTML = '<div class="ts">Synthesizing…</div>';
  try {
    const data = await api('/api/research/synthesis?slug=' + encodeURIComponent(slug) +
      '&q=' + encodeURIComponent(q) + '&max_sources=6');
    if (data.error) { out.innerHTML = `<div class="ts" style="color:#f85149">${escapeHtml(data.error)}</div>`; return; }
    const evs = (data.ranked_evidence || []).map(e => `<div class="synth-ev">
      <div class="head">
        <span class="id">${escapeHtml(e.evidence_id)}</span>
        <span>rel ${(e.relevance || 0).toFixed(2)}</span>
        <span>weight ${(e.weight || 0).toFixed(2)}</span>
      </div>
      <div class="claim">${escapeHtml(e.claim)}</div>
      ${e.sources && e.sources.length ? `<div class="srcs">${e.sources.map(escapeHtml).join(' · ')}</div>` : ''}
    </div>`).join('');
    const qs = (data.open_questions || []).map(q =>
      `<li><strong>${escapeHtml(q.id)}</strong> — ${escapeHtml(q.text)}</li>`).join('');
    const fu = (data.follow_up_suggestions || []).map(s => `<li>${escapeHtml(s)}</li>`).join('');
    const cons = (data.contradictions || []).map(c =>
      `<li><strong>${escapeHtml(c.claim_a_id)} ↔ ${escapeHtml(c.claim_b_id)}</strong> — ${escapeHtml(c.interpretation || '')}</li>`).join('');
    out.innerHTML =
      `<div class="ts">Question: ${escapeHtml(q)} · conf ${data.confidence_overall != null ? data.confidence_overall.toFixed(2) : '—'}</div>` +
      (evs ? `<h3 style="margin:14px 0 6px; font-size:13px; color:#e6edf3">Ranked evidence</h3>${evs}` :
        '<div class="ts" style="margin-top:8px">No evidence matched.</div>') +
      (qs ? `<h3 style="margin:14px 0 6px; font-size:13px; color:#e6edf3">Open questions</h3><ul>${qs}</ul>` : '') +
      (fu ? `<h3 style="margin:14px 0 6px; font-size:13px; color:#e6edf3">Suggested follow-ups</h3><ul>${fu}</ul>` : '') +
      (cons ? `<h3 style="margin:14px 0 6px; font-size:13px; color:#f85149">Contradictions</h3><ul>${cons}</ul>` : '');
  } catch (e) { out.innerHTML = `<div class="ts" style="color:#f85149">${escapeHtml(String(e))}</div>`; }
}

document.getElementById('synth-run')?.addEventListener('click', runSynth);
document.getElementById('synth-q')?.addEventListener('keydown', e => {
  if (e.key === 'Enter') runSynth();
});

async function loadSynth() { await loadSynthRecent(); }

// ---------- RAG ----------

async function loadRag() {
  const el = document.getElementById('rag-status');
  try {
    // Read the auto-rag state file directly. It's at C:\Data\Hermes\health\auto_rag_state.json
    // We can't directly fetch filesystem from a browser; proxy through /api/health/deep
    // which already aggregates health state, OR poll the watchdog state which our script
    // will write to. For now, just show project → notebook mirror state via the projects API.
    const data = await api('/api/research/projects');
    if (data.error) { el.innerHTML = `<div class="empty-state">⚠ ${escapeHtml(data.error)}</div>`; return; }
    const projs = data.projects || [];
    if (!projs.length) { el.innerHTML = '<div class="empty-state">No projects to mirror.</div>'; return; }
    el.innerHTML = projs.map(p => {
      const mirror = p.notebook_id ? '✓ mirrored' : '⚠ no notebook';
      const cls = p.notebook_id ? '' : 'overdue';
      return `<div class="synth-ev">
        <div class="head">
          <span class="id">${escapeHtml(p.slug)}</span>
          <span class="${cls}">${escapeHtml(mirror)}</span>
        </div>
        <div class="claim">${escapeHtml(p.title)}</div>
        <div class="srcs">E${p.evidence_total} evidence · H${p.hypotheses.length} hypotheses${p.notebook_id ? ' · notebook ' + escapeHtml(p.notebook_id) : ''}</div>
      </div>`;
    }).join('');
  } catch (e) { el.innerHTML = `<div class="empty-state">⚠ ${escapeHtml(String(e))}</div>`; }
}

// ---------- Bootstrap ----------

async function bootstrap() {
  const tok = await getSessionToken();
  if (tok) {
    setBadge('auth-badge', 'auth: ok', 'ok');
  } else {
    setBadge('auth-badge', 'auth: loopback-only', 'empty');
  }
  await loadBriefing();
  await loadSynth();
  await loadRag();
  setInterval(async () => {
    const activeTab = document.querySelector('.tab-btn.active')?.dataset.tab;
    if (activeTab === 'briefing') await loadBriefing();
    else if (activeTab === 'synth') await loadSynth();
    else if (activeTab === 'rag') await loadRag();
  }, POLL_MS);
}

bootstrap();
