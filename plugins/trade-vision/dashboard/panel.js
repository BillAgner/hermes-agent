// Trade Vision inner panel (runs inside the iframe)
// Fetches data and renders the dashboard

const POLL_MS = 30000;
let _token = null;

async function getSessionToken() {
  if (_token) return _token;
  try {
    const r = await fetch('/', { cache: 'no-store' });
    if (!r.ok) return null;
    const html = await r.text();
    const m = html.match(/window\.__HERMES_SESSION_TOKEN__\s*=\s*"([^"]+)"/);
    if (m) {
      _token = m[1];
      window.__HERMES_SESSION_TOKEN__ = _token;
     return _token;
    }
  } catch (e) {}
  return null;
}

async function api(path) {
  const token = await getSessionToken();
  const headers = {};
  if (token) headers['X-Hermes-Session-Token'] = token;
  const r = await fetch(path, { headers, cache: 'no-store' });
  if (!r.ok) return { error: 'HTTP ' + r.status, _status: r.status };
  return r.json();
}

async function readFile(path) {
  const r = await api('/api/files/read?path=' + encodeURIComponent(path));
  if (r.error || !r.data_url) return null;
  const m = r.data_url.match(/^data:[^;]+;base64,(.*)$/);
  if (!m) return null;
  try {
    const decoded = atob(m[1]);
    try { return JSON.parse(decoded); } catch (e) { return decoded; }
  } catch (e) { return null; }
}

async function tvApi(path, options) {
  options = options || {};
  try {
    const r = await fetch('http://127.0.0.1:9118' + path, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
    if (!r.ok) return { error: 'HTTP ' + r.status };
    return r.json();
  } catch (e) { return { error: e.message }; }
}

function setText(id, text) { const el = document.getElementById(id); if (el) el.textContent = text; }
function setHTML(id, html) { const el = document.getElementById(id); if (el) el.innerHTML = html; }
function setBadge(id, text, cls) { const el = document.getElementById(id); if (!el) return; el.textContent = text; el.className = 'badge ' + (cls || 'empty'); }

function escapeHtml(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, function(c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}

function fmtNum(n, digits) {
  digits = digits == null ? 2 : digits;
  if (n == null || isNaN(n)) return '-';
  return Number(n).toFixed(digits);
}

async function loadMarketRegime() {
  try {
    const digest = await readFile('C:/Data/Hermes/skills/trade-vision/data/daily_digest_2026-06-22.md');
    if (digest && typeof digest === 'string') {
      const m = digest.match(/## Market Regime([\s\S]*?)(?=---|## )/);
      if (m) {
        const lines = m[1].trim().split('\n').map(function(l) { return l.trim(); }).filter(function(l) { return l.startsWith('-'); });
        const html = lines.map(function(line) {
          const mm = line.match(/-\s*\*\*(.+?)\*\*:?\s*(.+)/);
          if (mm) return '<div class="k">' + escapeHtml(mm[1]) + '</div><div class="v">' + escapeHtml(mm[2]) + '</div>';
          return '<div class="k"></div><div class="v">' + escapeHtml(line.replace(/^-\s*/, '')) + '</div>';
        }).join('');
        setHTML('market-regime', html || '<div class="empty-state">No market data</div>');
        return;
      }
    }
    setHTML('market-regime', '<div class="empty-state">No digest yet - run daily_analysis.py</div>');
  } catch (e) {
    setHTML('market-regime', '<div class="error-state">' + escapeHtml(e.message) + '</div>');
  }
}

async function loadPortfolio() {
  const portfolio = [
    ['TSLA', '6800 sh'], ['MSTR', '1430 sh'], ['AGQ', '300 sh'],
    ['SOL', '1000'], ['HBAR', '750000'], ['BTC', '4.5'],
  ];
  const html = portfolio.map(function(p) {
    return '<div class="k">' + escapeHtml(p[0]) + '</div><div class="v">' + escapeHtml(p[1]) + '</div>';
  }).join('');
  setHTML('portfolio', html);
}

async function loadMarkov() {
  try {
    const data = await readFile('C:/Data/Hermes/skills/trade-vision/data/markov_latest.json');
    if (!data || typeof data !== 'object') {
      setHTML('markov-grid', '<div class="empty-state">Markov not yet computed</div>');
      return;
    }
    const tickers = ['TSLA', 'MSTR', 'AGQ'];
    const html = tickers.map(function(ticker) {
      const m = data[ticker];
      if (!m) return '<div class="k">' + ticker + '</div><div class="v">-</div>';
      const stateColor = m.current_state === 'UP' ? 'var(--green)' :
                         m.current_state === 'DOWN' ? 'var(--red)' : 'var(--yellow)';
      const pUpColor = m.p_up_3d > 0.5 ? 'var(--green)' : 'var(--red)';
      return '<div class="k"><strong>' + escapeHtml(ticker) + '</strong></div>' +
             '<div class="v">' +
             '<span style="color: ' + stateColor + '">' + escapeHtml(m.current_state) + '</span>' +
             ' P(up 3d)=<span style="color: ' + pUpColor + '">' + fmtNum(m.p_up_3d, 3) + '</span>' +
             '</div>';
    }).join('');
    setHTML('markov-grid', html);
  } catch (e) {
    setHTML('markov-grid', '<div class="error-state">' + escapeHtml(e.message) + '</div>');
  }
}

function getRecentDates(days) {
  const dates = [];
  const now = new Date();
  for (let i = 1; i <= days; i++) {
    const d = new Date(now);
    d.setDate(d.getDate() - i);
    dates.push(d.toISOString().slice(0, 10));
  }
  return dates;
}

function parseDigest(text) {
  const sections = {};
  const tickerRe = /^### (TSLA|MSTR|AGQ)\s+\(([^)]+)\)/gm;
  const matches = [];
  let m;
  while ((m = tickerRe.exec(text)) !== null) matches.push(m);
  for (let i = 0; i < matches.length; i++) {
    const ticker = matches[i][1];
    const startIdx = matches[i].index + matches[i][0].length;
    const endIdx = i + 1 < matches.length ? matches[i + 1].index : text.length;
    const sectionText = text.slice(startIdx, endIdx);
    let warning = '';
    const warnMatch = sectionText.match(/\*\*AGQ vol-decay warning[^]*?\*\*([^*]+)\*\*/);
    if (warnMatch) warning = warnMatch[1].trim();
    const recs = [];
    const tableMatch = sectionText.match(/\|[^\n]+\|[\s\S]*?\n([\s\S]*?)(?=\n\n|\n---|\n###|$)/);
    if (tableMatch) {
      const rows = tableMatch[1].trim().split('\n');
      for (const row of rows) {
        if (!row.startsWith('|')) continue;
        const cells = row.split('|').map(function(c) { return c.trim(); }).filter(function(c) { return c; });
        if (cells.length < 8) continue;
        if (cells[0].startsWith('---')) continue;
        const strike = parseFloat(cells[0].replace(/[$,]/g, ''));
        if (isNaN(strike)) continue;
        recs.push({
          strike: strike,
          expiration: cells[1],
          dte: parseInt(cells[2]),
          delta: parseFloat(cells[3]),
          buffer_atr: parseFloat(cells[4]),
          p_itm_adjusted: parseFloat(cells[6].replace('%', '')) / 100,
          premium_pct: parseFloat(cells[7].replace('%', '')) / 100,
          score: parseFloat(cells[8]),
        });
      }
    }
    sections[ticker] = { recs: recs, warning: warning };
  }
  return sections;
}

async function loadTodayRecs() {
  try {
    const today = new Date().toISOString().slice(0, 10);
    const candidates = [today].concat(getRecentDates(7));
    let digest = null;
    for (const d of candidates) {
      const path = 'C:/Data/Hermes/skills/trade-vision/data/daily_digest_' + d + '.md';
      const content = await readFile(path);
      if (content && typeof content === 'string' && content.length > 100) {
        digest = { date: d, content: content };
        break;
      }
    }
    if (!digest) {
      setHTML('today-recs', '<div class="empty-state">No digest yet</div>');
      return;
    }
    const sections = parseDigest(digest.content);
    const tickers = ['TSLA', 'MSTR', 'AGQ'];
    let html = '<p class="subtitle">From digest ' + escapeHtml(digest.date) + '</p>';
    for (const ticker of tickers) {
      const sec = sections[ticker];
      if (!sec) continue;
      html += '<div class="card"><h3>' + escapeHtml(ticker) + ' - ' + sec.recs.length + ' candidates</h3>';
      if (sec.warning) html += '<div class="warning-box">' + escapeHtml(sec.warning) + '</div>';
      if (sec.recs.length > 0) {
        html += '<table><thead><tr><th>Strike</th><th>Exp</th><th>DTE</th><th>d</th><th>Buffer</th><th>P(ITM)</th><th>Premium</th><th>Score</th></tr></thead><tbody>';
        for (const r of sec.recs) {
          html += '<tr><td><strong>$' + fmtNum(r.strike) + '</strong></td>' +
                  '<td>' + escapeHtml(r.expiration) + '</td>' +
                  '<td>' + r.dte + '</td>' +
                  '<td>' + fmtNum(r.delta, 3) + '</td>' +
                  '<td>' + fmtNum(r.buffer_atr, 2) + 'x</td>' +
                  '<td>' + fmtNum(r.p_itm_adjusted * 100, 1) + '%</td>' +
                  '<td>' + fmtNum(r.premium_pct * 100, 2) + '%</td>' +
                  '<td><strong>' + fmtNum(r.score, 1) + '</strong></td></tr>';
        }
        html += '</tbody></table>';
      } else {
        html += '<div class="empty-state">No qualifying trades</div>';
      }
      html += '</div>';
    }
    setHTML('today-recs', html);
  } catch (e) {
    setHTML('today-recs', '<div class="error-state">' + escapeHtml(e.message) + '</div>');
  }
}

async function loadJournalOpen() {
  const r = await tvApi('/api/trade-vision/trades?status=open');
  if (r.error) {
    setHTML('journal-open', '<div class="empty-state">Trade journal offline</div>');
    return;
  }
  const trades = r.trades || [];
  if (trades.length === 0) {
    setHTML('journal-open', '<div class="empty-state">No open positions</div>');
    return;
  }
  let html = '<table><thead><tr><th>Ticker</th><th>Strike</th><th>Exp</th><th>Contracts</th><th class="right">Premium</th><th>Intent</th></tr></thead><tbody>';
  for (const t of trades) {
    html += '<tr><td><strong>' + escapeHtml(t.ticker) + '</strong></td>' +
            '<td>$' + fmtNum(t.cc_strike) + '</td>' +
            '<td>' + escapeHtml(t.cc_expiration) + '</td>' +
            '<td>' + t.contracts + '</td>' +
            '<td class="right">$' + fmtNum(t.total_premium, 0) + '</td>' +
            '<td>' + escapeHtml(t.intent || '') + '</td></tr>';
  }
  html += '</tbody></table>';
  setHTML('journal-open', html);
}

async function loadJournalStats() {
  const r = await tvApi('/api/trade-vision/stats');
  if (r.error) {
    setHTML('journal-stats', '<div class="empty-state">Stats unavailable</div>');
    return;
  }
  const s = r.stats || {};
  const html = '<div class="kv-grid">' +
    '<div class="k">Total trades</div><div class="v">' + (s.total == null ? '-' : s.total) + '</div>' +
    '<div class="k">Win rate</div><div class="v">' + fmtNum(s.win_rate_pct, 1) + '%</div>' +
    '<div class="k">Total P&L</div><div class="v">$' + fmtNum(s.total_pnl, 0) + '</div>' +
    '<div class="k">Avg P&L</div><div class="v">$' + fmtNum(s.avg_pnl, 0) + '</div>' +
    '<div class="k">Total premium</div><div class="v">$' + fmtNum(s.total_premium, 0) + '</div>' +
    '<div class="k">Exit discipline</div><div class="v">' + fmtNum(s.exit_discipline_pct, 1) + '%</div>' +
    '</div>';
  setHTML('journal-stats', html);
}

async function loadDigest() {
  try {
    const today = new Date().toISOString().slice(0, 10);
    const candidates = [today].concat(getRecentDates(14));
    for (const d of candidates) {
      const path = 'C:/Data/Hermes/skills/trade-vision/data/daily_digest_' + d + '.md';
      const content = await readFile(path);
      if (content && typeof content === 'string' && content.length > 100) {
        setText('digest-meta', 'Daily digest: ' + d + ' (' + content.length + ' bytes)');
        setHTML('digest-content', escapeHtml(content));
        return;
      }
    }
    setText('digest-meta', 'No digest yet - run daily_analysis.py');
    setHTML('digest-content', '<div class="empty-state">No digest file found in last 14 days</div>');
  } catch (e) {
    setText('digest-meta', 'Error: ' + e.message);
  }
}

document.getElementById('log-trade-btn').addEventListener('click', function() {
  document.getElementById('log-trade-form').classList.remove('hidden');
});

document.getElementById('trade-form').addEventListener('submit', async function(e) {
  e.preventDefault();
  const data = {
    ticker: document.getElementById('f-ticker').value.toUpperCase(),
    action: document.getElementById('f-action').value,
    cc_strike: parseFloat(document.getElementById('f-strike').value),
    cc_expiration: document.getElementById('f-expiration').value,
    contracts: parseInt(document.getElementById('f-contracts').value),
    premium_per_share: parseFloat(document.getElementById('f-premium').value),
    intent: document.getElementById('f-intent').value,
    notes: document.getElementById('f-notes').value,
    entry_date: new Date().toISOString().slice(0, 10),
  };
  const r = await tvApi('/api/trade-vision/trades', {
    method: 'POST',
    body: JSON.stringify(data),
  });
  if (r.error) {
    alert('Error: ' + r.error);
  } else {
    alert('Trade logged (id=' + (r.trade && r.trade.id) + ')');
    document.getElementById('log-trade-form').classList.add('hidden');
    document.getElementById('trade-form').reset();
    loadJournalOpen();
    loadJournalStats();
  }
});

async function refreshAll() {
  await Promise.all([
    loadMarketRegime(),
    loadPortfolio(),
    loadMarkov(),
    loadTodayRecs(),
    loadJournalOpen(),
    loadJournalStats(),
    loadDigest(),
  ]);
  setText('last-update', new Date().toLocaleTimeString());
}

setBadge('auth-badge', 'auth: ok', 'ok');
refreshAll();
setInterval(refreshAll, POLL_MS);
