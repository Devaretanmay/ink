"""Self-contained static UI console assets for Ink.

Embedded single-page application requiring zero external network calls,
CDNs, or npm packages. Fully functional offline and air-gapped.
"""

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Ink Console — Behavior JIT Runtime</title>
  <style>
    :root {
      --bg: #000000;
      --card-bg: #050505;
      --card-border: #222222;
      --card-hover: #0c0c0c;
      --text-main: #ffffff;
      --text-muted: #888888;
      --text-dim: #444444;
      --primary: #ffffff;
      --primary-dim: rgba(255, 255, 255, 0.08);
      --success: #ffffff;
      --success-dim: rgba(255, 255, 255, 0.08);
      --warning: #aaaaaa;
      --warning-dim: rgba(170, 170, 170, 0.08);
      --danger: #ef4444;
      --danger-dim: rgba(239, 68, 68, 0.12);
      --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg);
      background-image: radial-gradient(circle, rgba(255, 255, 255, 0.08) 1px, transparent 1px);
      background-size: 14px 14px;
      color: var(--text-main);
      font-family: var(--font-sans);
      font-size: 13px;
      line-height: 1.5;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }
    header {
      background: #000000;
      border-bottom: 1px dotted var(--card-border);
      padding: 0.85rem 1.75rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      position: sticky;
      top: 0;
      z-index: 40;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      font-weight: 700;
      font-size: 1.15rem;
      letter-spacing: -0.02em;
    }
    .brand .logo {
      width: 24px;
      height: 24px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .nav-tabs {
      display: flex;
      gap: 0.4rem;
    }
    .nav-tab {
      background: transparent;
      border: 1px dotted transparent;
      color: var(--text-muted);
      padding: 0.45rem 0.9rem;
      border-radius: 4px;
      cursor: pointer;
      font-size: 12px;
      font-family: var(--font-mono);
      font-weight: 500;
      transition: all 0.15s ease;
    }
    .nav-tab:hover {
      color: var(--text-main);
      background: var(--card-hover);
      border-color: #333333;
    }
    .nav-tab.active {
      color: #ffffff;
      background: #111111;
      border-color: #ffffff;
    }
    .header-actions {
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }
    .badge-status {
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      padding: 0.2rem 0.6rem;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
      font-family: var(--font-mono);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .badge-status.active { background: #111111; color: #ffffff; border: 1px dotted #ffffff; }
    .badge-status.shadow { background: #0c0c0c; color: #888888; border: 1px dotted #444444; }
    .badge-status.observe { background: #0c0c0c; color: #888888; border: 1px dotted #333333; }
    .badge-status.danger { background: #1a0808; color: #f87171; border: 1px dotted #7f1d1d; }
    
    main {
      flex: 1;
      padding: 1.5rem 1.75rem;
      max-width: 1400px;
      width: 100%;
      margin: 0 auto;
    }
    .kpi-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 1rem;
      margin-bottom: 1.5rem;
    }
    .kpi-card {
      background: var(--card-bg);
      border: 1px dotted var(--card-border);
      border-radius: 6px;
      padding: 1.15rem;
      position: relative;
    }
    .kpi-card:hover {
      border-color: #444444;
    }
    .kpi-label {
      color: var(--text-muted);
      font-size: 12px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 0.35rem;
    }
    .kpi-value {
      font-size: 1.85rem;
      font-weight: 700;
      letter-spacing: -0.03em;
      color: var(--text-main);
    }
    .kpi-sub {
      font-size: 12px;
      color: var(--text-dim);
      margin-top: 0.25rem;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      margin-bottom: 1.5rem;
      overflow: hidden;
    }
    .card-header {
      padding: 0.9rem 1.25rem;
      border-bottom: 1px solid var(--card-border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      background: #0f121b;
    }
    .card-title {
      font-size: 14px;
      font-weight: 600;
    }
    .table-container {
      overflow-x: auto;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      text-align: left;
    }
    th {
      background: #0d1017;
      padding: 0.75rem 1rem;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-muted);
      border-bottom: 1px solid var(--card-border);
    }
    td {
      padding: 0.85rem 1rem;
      border-bottom: 1px solid var(--card-border);
      font-size: 13px;
    }
    tr.clickable:hover td {
      background: var(--card-hover);
      cursor: pointer;
    }
    .mono { font-family: var(--font-mono); }
    .btn {
      background: #1e2433;
      border: 1px solid #333b4f;
      color: var(--text-main);
      padding: 0.4rem 0.8rem;
      border-radius: 5px;
      cursor: pointer;
      font-size: 12px;
      font-weight: 500;
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      transition: all 0.15s ease;
    }
    .btn:hover { background: #273042; }
    .btn-primary {
      background: #0284c7;
      border-color: #38bdf8;
      color: white;
    }
    .btn-primary:hover { background: #0369a1; }
    
    /* Filters */
    .filter-bar {
      display: flex;
      gap: 0.6rem;
      align-items: center;
      flex-wrap: wrap;
      padding: 0.85rem 1.25rem;
      background: #0e111a;
      border-bottom: 1px solid var(--card-border);
    }
    .input-search {
      background: #090a0f;
      border: 1px solid var(--card-border);
      border-radius: 5px;
      color: var(--text-main);
      padding: 0.4rem 0.75rem;
      font-size: 13px;
      min-width: 240px;
    }
    .input-search:focus { outline: 1px solid var(--primary); }
    .select {
      background: #090a0f;
      border: 1px solid var(--card-border);
      border-radius: 5px;
      color: var(--text-main);
      padding: 0.4rem 0.75rem;
      font-size: 13px;
    }
    
    /* Drawer */
    .drawer-overlay {
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(0, 0, 0, 0.65);
      z-index: 50;
      display: none;
      backdrop-filter: blur(2px);
    }
    .drawer-overlay.open { display: block; }
    .drawer {
      position: fixed;
      top: 0; right: -650px; bottom: 0;
      width: 650px;
      max-width: 90vw;
      background: #0f121c;
      border-left: 1px solid var(--card-border);
      z-index: 60;
      transition: right 0.25s cubic-bezier(0.16, 1, 0.3, 1);
      display: flex;
      flex-direction: column;
      box-shadow: -10px 0 30px rgba(0, 0, 0, 0.5);
    }
    .drawer.open { right: 0; }
    .drawer-header {
      padding: 1.25rem;
      border-bottom: 1px solid var(--card-border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      background: #131724;
    }
    .drawer-content {
      flex: 1;
      padding: 1.25rem;
      overflow-y: auto;
    }
    .json-box {
      background: #08090e;
      border: 1px solid var(--card-border);
      border-radius: 6px;
      padding: 0.85rem;
      font-family: var(--font-mono);
      font-size: 12px;
      color: #93c5fd;
      overflow-x: auto;
      white-space: pre-wrap;
      word-break: break-all;
      margin-top: 0.5rem;
    }
    .timeline {
      display: flex;
      flex-direction: column;
      gap: 0.85rem;
      position: relative;
      margin: 1rem 0;
    }
    .timeline-item {
      display: flex;
      gap: 0.85rem;
      position: relative;
    }
    .timeline-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--primary);
      margin-top: 5px;
      flex-shrink: 0;
    }
    .timeline-body {
      flex: 1;
      background: #141824;
      border: 1px solid var(--card-border);
      border-radius: 6px;
      padding: 0.65rem 0.85rem;
    }
    .chip {
      display: inline-block;
      padding: 0.15rem 0.45rem;
      border-radius: 4px;
      font-size: 11px;
      font-family: var(--font-mono);
      background: #1c2233;
      color: #cbd5e1;
    }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="logo">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
          <circle cx="10" cy="3" r="1.2" fill="#fff"/>
          <circle cx="12" cy="3" r="1.2" fill="#fff"/>
          <circle cx="14" cy="3" r="1.2" fill="#fff"/>
          <circle cx="8" cy="6" r="1.2" fill="#888"/>
          <circle cx="10" cy="6" r="1.2" fill="#fff"/>
          <circle cx="12" cy="6" r="1.2" fill="#fff"/>
          <circle cx="14" cy="6" r="1.2" fill="#fff"/>
          <circle cx="16" cy="6" r="1.2" fill="#888"/>
          <circle cx="6" cy="10" r="1.4" fill="#fff"/>
          <circle cx="8" cy="10" r="1.4" fill="#fff"/>
          <circle cx="16" cy="10" r="1.4" fill="#fff"/>
          <circle cx="18" cy="10" r="1.4" fill="#fff"/>
          <circle cx="5" cy="14" r="1.4" fill="#fff"/>
          <circle cx="19" cy="14" r="1.4" fill="#fff"/>
          <circle cx="6" cy="18" r="1.4" fill="#fff"/>
          <circle cx="18" cy="18" r="1.4" fill="#fff"/>
          <circle cx="8" cy="21" r="1.4" fill="#fff"/>
          <circle cx="10" cy="21" r="1.4" fill="#fff"/>
          <circle cx="12" cy="21" r="1.4" fill="#fff"/>
          <circle cx="14" cy="21" r="1.4" fill="#fff"/>
          <circle cx="16" cy="21" r="1.4" fill="#fff"/>
          <path d="M12.5 8.5L9.5 13H13.5L11 17.5" stroke="#ffffff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </div>
      <span>INK Console</span>
      <span class="chip">v0.7.1</span>
    </div>
    <div class="nav-tabs">
      <button class="nav-tab active" onclick="switchTab('overview')">Overview</button>
      <button class="nav-tab" onclick="switchTab('sites')">Decision Sites</button>
      <button class="nav-tab" onclick="switchTab('explorer')">Decision Explorer</button>
      <button class="nav-tab" onclick="switchTab('controls')">Serving Controls</button>
    </div>
    <div class="header-actions">
      <button class="btn" onclick="refreshCurrentView()">↻ Refresh</button>
    </div>
  </header>

  <main>
    <!-- View 1: Overview -->
    <div id="view-overview">
      <div style="margin-bottom: 2rem; border-bottom: 1px dotted var(--card-border); padding-bottom: 1.5rem;">
        <div style="font-size: 2.25rem; font-weight: 800; letter-spacing: -0.03em; color: #ffffff;">It proves.</div>
        <div style="font-size: 13px; color: var(--text-muted); margin-top: 4px; letter-spacing: -0.01em;">Models handle novelty. Ink turns proven behavior into software.</div>
        <div style="display: flex; align-items: center; gap: 12px; margin-top: 14px;">
          <div style="flex: 1; border-top: 1px dotted #333333;"></div>
          <span style="font-family: var(--font-mono); font-size: 11px; color: #666666; text-transform: lowercase;">error budget</span>
        </div>
      </div>
      <div class="kpi-grid">
        <div class="kpi-card">
          <div class="kpi-label">Fast-Path Hit Rate</div>
          <div class="kpi-value" id="kpi-fast-rate">0%</div>
          <div class="kpi-sub" id="kpi-fast-count">0 of 0 decisions</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Estimated Token $ Saved</div>
          <div class="kpi-value" id="kpi-dollars-saved">$0.00</div>
          <div class="kpi-sub" id="kpi-model-calls-avoided">0 model calls avoided</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Active DecisionSites</div>
          <div class="kpi-value" id="kpi-active-sites">0</div>
          <div class="kpi-sub" id="kpi-sites-status">0 active, 0 shadow, 0 observe</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Verified Outcomes</div>
          <div class="kpi-value" id="kpi-verified-rate">100%</div>
          <div class="kpi-sub" id="kpi-verified-count">0 independent verifications</div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <div class="card-title">Registered DecisionSites</div>
        </div>
        <div class="table-container">
          <table>
            <thead>
              <tr>
                <th>Site Name</th>
                <th>Status</th>
                <th>Observations</th>
                <th>Fast-Path %</th>
                <th>Wilson Conf (95%)</th>
                <th>Verifier Risk</th>
                <th>Est. $ Saved</th>
              </tr>
            </thead>
            <tbody id="overview-sites-tbody">
              <tr><td colspan="7" style="text-align:center; color: var(--text-dim); padding: 2rem;">Loading registered sites...</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- View 2: Decision Sites -->
    <div id="view-sites" style="display:none;">
      <div class="card">
        <div class="card-header">
          <div class="card-title">All Decision Sites</div>
        </div>
        <div class="table-container">
          <table>
            <thead>
              <tr>
                <th>Site Contract</th>
                <th>Choices</th>
                <th>Status</th>
                <th>Observations</th>
                <th>Fast-Served</th>
                <th>Fallbacks</th>
                <th>Serving Engine</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody id="sites-tbody">
              <tr><td colspan="8" style="text-align:center; color: var(--text-dim); padding: 2rem;">Loading sites...</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- View 3: Decision Explorer (Trace Replay) -->
    <div id="view-explorer" style="display:none;">
      <div class="card">
        <div class="card-header">
          <div class="card-title">Decision Explorer & Traces</div>
          <div style="display:flex; align-items:center; gap:0.5rem;">
            <label style="font-size:12px; color:var(--text-muted); cursor:pointer;">
              <input type="checkbox" id="chk-redact" onchange="loadExplorer()" checked> Redact PII
            </label>
          </div>
        </div>
        <div class="filter-bar">
          <select id="filter-site" class="select" onchange="loadExplorer()">
            <option value="">All Sites</option>
          </select>
          <select id="filter-source" class="select" onchange="loadExplorer()">
            <option value="">All Sources</option>
            <option value="fast_path">Fast Path (<0.1ms)</option>
            <option value="fallback">Host Fallback</option>
          </select>
          <select id="filter-outcome" class="select" onchange="loadExplorer()">
            <option value="">All Outcomes</option>
            <option value="1">With Outcome Only</option>
            <option value="0">Pending Outcome Only</option>
          </select>
        </div>
        <div class="table-container">
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Site</th>
                <th>Choice</th>
                <th>Source</th>
                <th>Reason / Engine</th>
                <th>Outcome Quality</th>
                <th>Latency</th>
              </tr>
            </thead>
            <tbody id="explorer-tbody">
              <tr><td colspan="7" style="text-align:center; color: var(--text-dim); padding: 2rem;">Loading decision traces...</td></tr>
            </tbody>
          </table>
        </div>
        <div style="padding: 0.75rem 1.25rem; display: flex; justify-content: space-between; align-items: center; background: #0c0e15;">
          <span id="pagination-info" style="font-size: 12px; color: var(--text-muted);">Showing 0-0 of 0</span>
          <div style="display: flex; gap: 0.5rem;">
            <button class="btn" id="btn-prev" onclick="prevPage()">← Previous</button>
            <button class="btn" id="btn-next" onclick="nextPage()">Next →</button>
          </div>
        </div>
      </div>
    </div>

    <!-- View 4: Serving Controls -->
    <div id="view-controls" style="display:none;">
      <div class="card">
        <div class="card-header">
          <div class="card-title">Serving Authority & Canary Status</div>
        </div>
        <div style="padding: 1rem 1.25rem; font-size: 13px; color: var(--text-muted); border-bottom: 1px dotted var(--card-border);">
          Serving policies (<code>ACTIVE</code>, <code>CANARY</code>, <code>SHADOW</code>, <code>HOST_OVERRIDE</code>) are governed programmatically via the Python API or CLI for auditability. The local developer workbench is strictly read-only.
        </div>
        <div class="table-container">
          <table>
            <thead>
              <tr>
                <th>Site</th>
                <th>Artifact Status</th>
                <th>Serving Mode</th>
                <th>Canary Rate</th>
                <th>Last Updated</th>
                <th>Governance</th>
              </tr>
            </thead>
            <tbody id="controls-tbody">
              <tr><td colspan="6" style="text-align:center; color: var(--text-dim); padding: 2rem;">Loading serving policies...</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </main>

  <!-- Trace Inspector Drawer -->
  <div class="drawer-overlay" id="drawer-overlay" onclick="closeDrawer()"></div>
  <div class="drawer" id="drawer">
    <div class="drawer-header">
      <div>
        <div style="font-weight: 600; font-size: 15px;">Decision Trace Inspector</div>
        <div id="drawer-subtitle" class="mono" style="font-size: 11px; color: var(--text-muted); margin-top:2px;"></div>
      </div>
      <button class="btn" onclick="closeDrawer()">✕</button>
    </div>
    <div class="drawer-content" id="drawer-content">
      <!-- Populated dynamically -->
    </div>
  </div>

  <script>
    let currentTab = 'overview';
    let explorerOffset = 0;
    const explorerLimit = 25;
    let sitesCache = [];

    function switchTab(tab) {
      currentTab = tab;
      document.querySelectorAll('.nav-tab').forEach(b => {
        const onclickAttr = b.getAttribute('onclick') || '';
        b.classList.toggle('active', onclickAttr.includes(`'${tab}'`));
      });
      ['overview', 'sites', 'explorer', 'controls'].forEach(v => {
        document.getElementById(`view-${v}`).style.display = (v === tab) ? 'block' : 'none';
      });
      refreshCurrentView();
    }

    function refreshCurrentView() {
      if (currentTab === 'overview') loadOverview();
      else if (currentTab === 'sites') loadSites();
      else if (currentTab === 'explorer') loadExplorer();
      else if (currentTab === 'controls') loadControls();
    }

    function escapeHTML(str) {
      if (str === null || str === undefined) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    async function fetchJSON(url, opts = {}) {
      try {
        const res = await fetch(url, opts);
        return await res.json();
      } catch (err) {
        console.error('Fetch error:', err);
        return null;
      }
    }

    function inspectSite(encodedName) {
      const siteName = decodeURIComponent(encodedName);
      switchTab('explorer');
      const sel = document.getElementById('filter-site');
      if (sel) {
        sel.value = siteName;
        loadExplorer();
      }
    }

    async function loadOverview() {
      const [overview, sites] = await Promise.all([
        fetchJSON('/api/overview'),
        fetchJSON('/api/sites')
      ]);
      if (overview) {
        document.getElementById('kpi-fast-rate').innerText = `${Math.round(overview.fast_path_ratio * 100)}%`;
        document.getElementById('kpi-fast-count').innerText = `${overview.total_fast_path.toLocaleString()} of ${overview.total_decisions.toLocaleString()} decisions`;
        document.getElementById('kpi-dollars-saved').innerText = `$${overview.estimated_cost_saved.toFixed(2)}`;
        document.getElementById('kpi-model-calls-avoided').innerText = `${overview.model_calls_avoided.toLocaleString()} model calls avoided`;
        document.getElementById('kpi-active-sites').innerText = overview.sites_count;
        const b = overview.status_breakdown || {};
        document.getElementById('kpi-sites-status').innerText = `${b.ACTIVE || 0} active, ${b.SHADOW || 0} shadow, ${b.OBSERVE || 0} observe`;
      }
      if (sites) {
        sitesCache = sites;
        const tbody = document.getElementById('overview-sites-tbody');
        tbody.innerHTML = sites.map(s => {
          const safeName = escapeHTML(s.name);
          const encodedName = encodeURIComponent(s.name);
          const safeState = escapeHTML(s.state);
          const safeRisk = escapeHTML(s.verifier_risk_level || 'low');
          const safeEvidence = s.profile && s.profile.evidence ? escapeHTML(String(s.profile.evidence.quality_wilson_lower || '—')) : '—';
          return `
            <tr class="clickable" onclick="inspectSite('${encodedName}')">
              <td class="mono"><strong>${safeName}</strong></td>
              <td><span class="badge-status ${safeState.toLowerCase()}">${safeState}</span></td>
              <td>${Number(s.observations || 0).toLocaleString()}</td>
              <td>${Math.round(Number(s.coverage || 0) * 100)}%</td>
              <td class="mono">${safeEvidence}</td>
              <td><span class="chip">${safeRisk}</span></td>
              <td>$${(Number(s.estimated_cost_saved) || 0).toFixed(4)}</td>
            </tr>
          `;
        }).join('') || '<tr><td colspan="7" style="text-align:center; padding: 2rem;">No sites registered yet</td></tr>';
      }
    }

    async function loadSites() {
      const sites = await fetchJSON('/api/sites');
      const tbody = document.getElementById('sites-tbody');
      tbody.innerHTML = (sites || []).map(s => {
        const safeName = escapeHTML(s.name);
        const encodedName = encodeURIComponent(s.name);
        const safeRev = escapeHTML(s.active_revision || 'no active artifact');
        const safeState = escapeHTML(s.state);
        const safeEngine = escapeHTML(s.selected_engine || 'none');
        const safeChoices = (s.choices ? Object.keys(s.choices) : []).map(c => `<span class="chip">${escapeHTML(c)}</span>`).join(' ');
        return `
          <tr>
            <td><div class="mono" style="font-weight:600;">${safeName}</div><div class="mono" style="font-size:10px; color:var(--text-dim);">${safeRev}</div></td>
            <td>${safeChoices}</td>
            <td><span class="badge-status ${safeState.toLowerCase()}">${safeState}</span></td>
            <td>${Number(s.observations || 0)}</td>
            <td>${Number(s.fallbacks_avoided || 0)}</td>
            <td>${Number(s.fallbacks || 0)}</td>
            <td><span class="chip">${safeEngine}</span></td>
            <td><button class="btn" onclick="inspectSite('${encodedName}')">Inspect</button></td>
          </tr>
        `;
      }).join('') || '<tr><td colspan="8" style="text-align:center; padding: 2rem;">No sites found</td></tr>';
    }

    async function loadExplorer() {
      const site = document.getElementById('filter-site').value;
      const source = document.getElementById('filter-source').value;
      const outcome = document.getElementById('filter-outcome').value;
      const redact = document.getElementById('chk-redact').checked ? 1 : 0;
      
      const query = new URLSearchParams({
        limit: explorerLimit,
        offset: explorerOffset,
        redact: redact,
      });
      if (site) query.set('site', site);
      if (source) query.set('source', source);
      if (outcome) query.set('has_outcome', outcome);

      const res = await fetchJSON(`/api/decisions?${query}`);
      const tbody = document.getElementById('explorer-tbody');
      if (!res || !res.decisions) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding: 2rem;">No decisions found</td></tr>';
        return;
      }
      document.getElementById('pagination-info').innerText = `Showing ${res.decisions.length ? explorerOffset + 1 : 0}-${explorerOffset + res.decisions.length} of ${res.total}`;
      document.getElementById('btn-prev').disabled = (explorerOffset === 0);
      document.getElementById('btn-next').disabled = (explorerOffset + explorerLimit >= res.total);

      tbody.innerHTML = res.decisions.map(d => {
        const encodedId = encodeURIComponent(d.id);
        const safeSite = escapeHTML(d.site_name || (d.site ? d.site.slice(0, 8) : ''));
        const safeChoice = escapeHTML(d.choice);
        const safeSource = d.source === 'fast_path' ? 'Fast Path' : 'Fallback';
        const safeSourceClass = d.source === 'fast_path' ? 'active' : 'shadow';
        const safeReason = escapeHTML(d.reason || d.prediction?.producer || '—');
        const safeOutcome = d.outcome ? (d.outcome.quality === 1 ? '✅ 1.0' : '⚠️ ' + escapeHTML(String(d.outcome.quality))) : '<span style="color:var(--text-dim);">pending</span>';
        const elapsedMs = (Number(d.elapsed || 0) * 1000).toFixed(2);
        return `
          <tr class="clickable" onclick="inspectDecision('${encodedId}')">
            <td class="mono" style="font-size:11px; color:var(--text-muted);">${new Date(d.created * 1000).toLocaleTimeString()}</td>
            <td class="mono"><strong>${safeSite}</strong></td>
            <td><span class="chip" style="color:var(--primary);">${safeChoice}</span></td>
            <td><span class="badge-status ${safeSourceClass}">${safeSource}</span></td>
            <td><span class="chip">${safeReason}</span></td>
            <td>${safeOutcome}</td>
            <td class="mono">${elapsedMs}ms</td>
          </tr>
        `;
      }).join('') || '<tr><td colspan="7" style="text-align:center; padding: 2rem;">No decisions found for filters</td></tr>';

      // Update site filter options if needed
      const sel = document.getElementById('filter-site');
      if (sel.options.length <= 1 && sitesCache.length) {
        sitesCache.forEach(s => {
          const opt = document.createElement('option');
          opt.value = s.site_version || s.name;
          opt.innerText = s.name;
          sel.appendChild(opt);
        });
      }
    }

    function prevPage() {
      if (explorerOffset >= explorerLimit) {
        explorerOffset -= explorerLimit;
        loadExplorer();
      }
    }

    function nextPage() {
      explorerOffset += explorerLimit;
      loadExplorer();
    }

    async function inspectDecision(encodedId) {
      const id = decodeURIComponent(encodedId);
      const d = await fetchJSON(`/api/decisions/${encodeURIComponent(id)}`);
      if (!d) return;
      document.getElementById('drawer-subtitle').innerText = `ID: ${d.id}`;
      const content = document.getElementById('drawer-content');
      const safeChoice = escapeHTML(d.choice);
      const safeOutcome = d.outcome
        ? `Quality: ${escapeHTML(String(d.outcome.quality))} (Verifier: ${escapeHTML(d.outcome.verifier || 'observed')})`
        : 'No verified outcome recorded yet';
      const safeStateJson = escapeHTML(JSON.stringify(d.state, null, 2));
      const safeReceiptJson = escapeHTML(JSON.stringify(d.receipt || d.prediction?.receipt || {}, null, 2));
      const encodedDecisionId = encodeURIComponent(d.id);
      const safeSource = d.source === 'fast_path' ? 'FAST PATH SERVED' : 'HOST FALLBACK';
      const safeSourceClass = d.source === 'fast_path' ? 'active' : 'shadow';
      const elapsedMs = (Number(d.elapsed || 0) * 1000).toFixed(3);

      content.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem;">
          <span class="badge-status ${safeSourceClass}">${safeSource}</span>
          <span class="mono" style="font-size:12px; color:var(--text-muted);">${elapsedMs}ms</span>
        </div>

        <div style="font-weight:600; margin-top:1rem; font-size:13px;">Decision Outcome</div>
        <div style="padding:0.75rem; background:#141824; border-radius:6px; border:1px solid var(--card-border); margin-top:0.35rem;">
          <div>Choice: <strong style="color:var(--primary);">${safeChoice}</strong></div>
          <div style="font-size:12px; color:var(--text-muted); margin-top:4px;">
            ${safeOutcome}
          </div>
        </div>

        <div style="font-weight:600; margin-top:1.25rem; font-size:13px;">Input State Payload</div>
        <div class="json-box">${safeStateJson}</div>

        <div style="font-weight:600; margin-top:1.25rem; font-size:13px;">Provenance & Verification Receipt</div>
        <div class="json-box">${safeReceiptJson}</div>

        <div style="margin-top:1.5rem; padding-top:1rem; border-top:1px solid var(--card-border); display:flex; gap:0.5rem;">
          <button class="btn btn-primary" onclick="simulateReplay('${encodedDecisionId}')">⚡ Inspect In-Memory Prediction</button>
        </div>
        <div id="replay-result" style="margin-top:0.75rem;"></div>
      `;
      openDrawer();
    }

    async function simulateReplay(encodedId) {
      const id = decodeURIComponent(encodedId);
      const container = document.getElementById('replay-result');
      container.innerHTML = '<span style="color:var(--text-muted);">Evaluating in-memory engine prediction against state...</span>';
      const res = await fetchJSON(`/api/decisions/${encodeURIComponent(id)}/replay`, { method: 'POST' });
      if (!res) {
        container.innerHTML = '<span style="color:var(--danger);">Prediction inspection failed</span>';
        return;
      }
      const safeLabel = escapeHTML(res.provenance_label || 'In-Memory Prediction');
      const safeChoice = escapeHTML(res.candidate_choice || '—');
      const matchText = res.matches_historical ? '✅ Yes' : '⚠️ No (Disagreement)';
      container.innerHTML = `
        <div style="padding:0.75rem; background:#181f2f; border:1px solid var(--primary); border-radius:6px;">
          <div style="font-weight:600; font-size:12px; color:var(--primary); margin-bottom:4px;">
            ${safeLabel}
          </div>
          <div>Predicted Choice: <strong>${safeChoice}</strong></div>
          <div style="font-size:12px; color:var(--text-muted); margin-top:2px;">
            Matches historical choice: ${matchText}
          </div>
          <div style="font-size:11px; color:var(--text-muted); margin-top:4px;">
            Deterministic in-process engine prediction only. External callbacks, verifiers, and downstream network side effects are not executed.
          </div>
        </div>
      `;
    }

    async function loadControls() {
      const sites = await fetchJSON('/api/sites');
      const tbody = document.getElementById('controls-tbody');
      tbody.innerHTML = (sites || []).map(s => {
        const pol = s.serving_policy || { mode: 'ACTIVE', canary_rate: 1.0, updated_at: 0 };
        const safeName = escapeHTML(s.name);
        const safeState = escapeHTML(s.state);
        const safeMode = escapeHTML(pol.mode);
        const canaryPct = Math.round(Number(pol.canary_rate || 1.0) * 100);
        const updatedStr = pol.updated_at ? escapeHTML(new Date(pol.updated_at * 1000).toLocaleString()) : 'default';
        return `
          <tr>
            <td class="mono"><strong>${safeName}</strong></td>
            <td><span class="badge-status ${safeState.toLowerCase()}">${safeState}</span></td>
            <td><span class="chip">${safeMode}</span></td>
            <td>${canaryPct}%</td>
            <td style="font-size:11px; color:var(--text-muted);">${updatedStr}</td>
            <td style="font-size:11px; color:var(--text-muted);">Python API / CLI</td>
          </tr>
        `;
      }).join('') || '<tr><td colspan="6" style="text-align:center; padding: 2rem;">No sites found</td></tr>';
    }

    function openDrawer() {
      document.getElementById('drawer-overlay').classList.add('open');
      document.getElementById('drawer').classList.add('open');
    }

    function closeDrawer() {
      document.getElementById('drawer-overlay').classList.remove('open');
      document.getElementById('drawer').classList.remove('open');
    }

    // Initial boot
    loadOverview();
  </script>
</body>
</html>
"""
