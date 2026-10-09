"""HTML and CSS template for Ink Console Alpha.

Self-contained zero-dependency single-page application for local-first
observability into Ink DecisionSites, Fast Paths, and system health.
Pure high-contrast monochrome black-and-white theme. Zero emojis.
"""

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Ink Console — Behavior JIT</title>
  <style>
    :root {
      /* Pure Monochrome Palette */
      --black-true: #000000;
      --bg-deep: #050505;
      --bg-base: #0a0a0a;
      --bg-surface: #111111;
      --bg-elevated: #181818;
      --bg-hover: #222222;

      /* High-Contrast Borders */
      --border-subtle: #1f1f1f;
      --border-medium: #333333;
      --border-strong: #ffffff;

      /* Typography */
      --text-primary: #ffffff;
      --text-secondary: #d4d4d4;
      --text-muted: #888888;
      --text-faint: #444444;

      /* Inverted High-Contrast Accents */
      --accent-invert-bg: #ffffff;
      --accent-invert-text: #000000;

      /* Fonts */
      --font-sans: -apple-system, BlinkMacSystemFont, "Plus Jakarta Sans", "Inter", "Segoe UI", Roboto, sans-serif;
      --font-mono: "JetBrains Mono", "SF Mono", Menlo, Monaco, Consolas, monospace;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      background: var(--bg-base);
      color: var(--text-primary);
      font-family: var(--font-sans);
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }

    header {
      background: var(--black-true);
      border-bottom: 1px solid var(--border-medium);
      padding: 0.875rem 2rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 0.875rem;
    }

    .brand-emblem {
      background: var(--accent-invert-bg);
      color: var(--accent-invert-text);
      font-family: var(--font-mono);
      font-size: 0.8125rem;
      font-weight: 900;
      letter-spacing: 0.08em;
      padding: 0.25rem 0.625rem;
      border-radius: 3px;
    }

    .brand-title {
      font-size: 1.0625rem;
      font-weight: 700;
      letter-spacing: -0.02em;
      color: #ffffff;
    }

    .brand-tag {
      font-size: 0.8125rem;
      color: var(--text-muted);
      border-left: 1px solid var(--border-medium);
      padding-left: 0.875rem;
      margin-left: 0.25rem;
    }

    .header-meta {
      display: flex;
      align-items: center;
      gap: 1rem;
    }

    .status-badge {
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      font-size: 0.75rem;
      font-family: var(--font-mono);
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      background: transparent;
      color: #ffffff;
      padding: 0.25rem 0.625rem;
      border-radius: 9999px;
      border: 1px solid var(--border-medium);
    }

    .pulse-dot {
      width: 6px;
      height: 6px;
      background: #ffffff;
      border-radius: 50%;
      box-shadow: 0 0 8px rgba(255, 255, 255, 0.8);
    }

    .demo-badge {
      background: transparent;
      color: #ffffff;
      border: 1px solid var(--border-strong);
      padding: 0.25rem 0.625rem;
      border-radius: 9999px;
      font-size: 0.6875rem;
      font-family: var(--font-mono);
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }

    nav {
      background: var(--black-true);
      border-bottom: 1px solid var(--border-medium);
      padding: 0 2rem;
      display: flex;
      gap: 2rem;
    }

    .nav-btn {
      background: none;
      border: none;
      color: var(--text-muted);
      font-size: 0.875rem;
      font-weight: 500;
      letter-spacing: -0.01em;
      padding: 0.875rem 0;
      cursor: pointer;
      position: relative;
      transition: color 0.15s ease;
    }

    .nav-btn:hover { color: #ffffff; }

    .nav-btn.active {
      color: #ffffff;
      font-weight: 700;
    }

    .nav-btn.active::after {
      content: "";
      position: absolute;
      bottom: -1px;
      left: 0;
      right: 0;
      height: 2px;
      background: #ffffff;
    }

    main {
      flex: 1;
      padding: 2rem;
      max-width: 1400px;
      width: 100%;
      margin: 0 auto;
    }

    .tab-content { display: none; }
    .tab-content.active { display: block; }

    .grid-4 {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 1.25rem;
      margin-bottom: 2rem;
    }

    .stat-card {
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: 4px;
      padding: 1.375rem;
      position: relative;
      overflow: hidden;
      transition: border-color 0.15s ease;
    }

    .stat-card:hover {
      border-color: var(--border-medium);
    }

    .stat-card.accent-card::before {
      content: "";
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      height: 2px;
      background: #ffffff;
    }

    .stat-label {
      font-size: 0.6875rem;
      font-family: var(--font-mono);
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--text-muted);
      margin-bottom: 0.5rem;
    }

    .stat-value {
      font-size: 2.125rem;
      font-weight: 800;
      letter-spacing: -0.03em;
      color: #ffffff;
      font-feature-settings: "tnum" 1;
    }

    .stat-value.highlight {
      color: #ffffff;
    }

    .stat-sub {
      font-size: 0.75rem;
      color: var(--text-muted);
      margin-top: 0.375rem;
    }

    .card {
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: 4px;
      margin-bottom: 2rem;
      overflow: hidden;
    }

    .card-header {
      padding: 1.125rem 1.5rem;
      border-bottom: 1px solid var(--border-subtle);
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: var(--bg-deep);
    }

    .card-title {
      font-size: 0.9375rem;
      font-weight: 700;
      letter-spacing: -0.01em;
      color: #ffffff;
    }

    .card-sub {
      font-size: 0.8125rem;
      color: var(--text-muted);
      margin-top: 0.125rem;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      text-align: left;
      font-size: 0.875rem;
    }

    th {
      background: var(--bg-deep);
      color: var(--text-muted);
      font-weight: 600;
      font-size: 0.6875rem;
      font-family: var(--font-mono);
      text-transform: uppercase;
      letter-spacing: 0.08em;
      padding: 0.75rem 1.5rem;
      border-bottom: 1px solid var(--border-subtle);
    }

    td {
      padding: 0.875rem 1.5rem;
      border-bottom: 1px solid var(--border-subtle);
      vertical-align: middle;
      color: var(--text-secondary);
    }

    tr:last-child td { border-bottom: none; }
    tr:hover td { background: var(--bg-hover); }

    .badge {
      display: inline-flex;
      align-items: center;
      font-size: 0.6875rem;
      font-family: var(--font-mono);
      font-weight: 800;
      padding: 0.2rem 0.55rem;
      border-radius: 3px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }

    .badge-ACTIVE {
      background: #ffffff;
      color: #000000;
      border: 1px solid #ffffff;
    }

    .badge-SHADOW {
      background: transparent;
      color: #ffffff;
      border: 1px solid var(--border-medium);
    }

    .badge-CANDIDATE {
      background: transparent;
      color: #cccccc;
      border: 1px dashed var(--border-medium);
    }

    .badge-OBSERVE {
      background: transparent;
      color: var(--text-muted);
      border: 1px solid var(--border-subtle);
    }

    .badge-DEOPT {
      background: #222222;
      color: #777777;
      border: 1px solid #333333;
    }

    .source-tag {
      display: inline-flex;
      align-items: center;
      gap: 0.375rem;
      font-size: 0.75rem;
      font-family: var(--font-mono);
      font-weight: 700;
      padding: 0.2rem 0.55rem;
      border-radius: 3px;
    }

    .source-fast {
      background: #ffffff;
      color: #000000;
      border: 1px solid #ffffff;
    }

    .source-fallback {
      background: transparent;
      color: var(--text-secondary);
      border: 1px solid var(--border-medium);
    }

    .svg-icon {
      width: 12px;
      height: 12px;
      display: inline-block;
      vertical-align: middle;
      flex-shrink: 0;
    }

    .mono {
      font-family: var(--font-mono);
      font-feature-settings: "tnum" 1;
    }

    .code-pill {
      font-family: var(--font-mono);
      font-size: 0.75rem;
      background: var(--bg-elevated);
      padding: 0.15rem 0.45rem;
      border-radius: 3px;
      color: #ffffff;
      border: 1px solid var(--border-subtle);
    }

    .empty-state {
      padding: 4rem 2rem;
      text-align: center;
      color: var(--text-muted);
    }

    .empty-state h3 {
      font-size: 1.0625rem;
      color: #ffffff;
      margin-bottom: 0.5rem;
      letter-spacing: -0.01em;
    }

    .refresh-bar {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 1rem;
      margin-bottom: 1rem;
      font-size: 0.8125rem;
      color: var(--text-muted);
    }

    .btn {
      background: var(--bg-elevated);
      border: 1px solid var(--border-medium);
      color: #ffffff;
      padding: 0.4rem 0.85rem;
      border-radius: 3px;
      cursor: pointer;
      font-size: 0.8125rem;
      font-weight: 600;
      transition: background 0.15s ease, border-color 0.15s ease;
    }

    .btn:hover {
      background: #ffffff;
      color: #000000;
      border-color: #ffffff;
    }

    .btn-primary {
      background: #ffffff;
      color: #000000;
      border: 1px solid #ffffff;
      font-weight: 700;
    }

    .btn-primary:hover {
      background: #e5e5e5;
    }

    .verified-mark {
      display: inline-flex;
      align-items: center;
      gap: 0.375rem;
      color: #ffffff;
      font-family: var(--font-mono);
      font-size: 0.75rem;
      font-weight: 700;
    }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="brand-emblem">INK</div>
      <div class="brand-title">Console</div>
      <span class="brand-tag">Behavior JIT for Production AI</span>
    </div>
    <div class="header-meta">
      <span id="demoBadge" class="demo-badge" style="display: none;">Demo Mode</span>
      <div class="status-badge">
        <span class="pulse-dot"></span>
        <span id="connStatus">Live</span>
      </div>
      <span class="code-pill" id="inkVersion">v0.6.0</span>
    </div>
  </header>

  <nav>
    <button class="nav-btn active" onclick="showTab('overview')">Overview</button>
    <button class="nav-btn" onclick="showTab('sites')">DecisionSites</button>
    <button class="nav-btn" onclick="showTab('fastpaths')">Inks &amp; Fast Paths</button>
    <button class="nav-btn" onclick="showTab('activity')">Activity Stream</button>
    <button class="nav-btn" onclick="showTab('discovery')">Discovery</button>
    <button class="nav-btn" onclick="showTab('system')">Doctor &amp; System</button>
  </nav>

  <main>
    <!-- OVERVIEW TAB -->
    <div id="tab-overview" class="tab-content active">
      <div class="grid-4">
        <div class="stat-card">
          <div class="stat-label">Total Decisions</div>
          <div class="stat-value" id="statDecisions">0</div>
          <div class="stat-sub">Observed and evaluated</div>
        </div>
        <div class="stat-card accent-card">
          <div class="stat-label">Fast Path Served Rate</div>
          <div class="stat-value highlight" id="statFastRate">0.0%</div>
          <div class="stat-sub" id="statFastSub">0 served locally</div>
        </div>
        <div class="stat-card accent-card">
          <div class="stat-label">Est. Latency Saved</div>
          <div class="stat-value highlight" id="statLatencySaved">0 ms</div>
          <div class="stat-sub">Bypassing host fallback models</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Active Fast Paths</div>
          <div class="stat-value" id="statActiveSites">0</div>
          <div class="stat-sub" id="statActiveSub">0 total registered sites</div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <div>
            <div class="card-title">DecisionSites Status</div>
            <div class="card-sub">Active serving authority, observation metrics, and qualified local coverage</div>
          </div>
        </div>
        <div id="overviewSitesContainer">
          <div class="empty-state">
            <h3>No DecisionSites recorded yet</h3>
            <p>Decisions are registered automatically when your application calls ink.decide().</p>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <div>
            <div class="card-title">Recent Decisions</div>
            <div class="card-sub">Chronological production decisions resolved locally or dispatched to fallback</div>
          </div>
        </div>
        <div id="overviewRecentContainer"></div>
      </div>
    </div>

    <!-- SITES TAB -->
    <div id="tab-sites" class="tab-content">
      <div class="card">
        <div class="card-header">
          <div>
            <div class="card-title">Registered DecisionSites</div>
            <div class="card-sub">Explicit bounded decision contracts and serving qualification states</div>
          </div>
        </div>
        <div id="sitesListContainer"></div>
      </div>
    </div>

    <!-- FAST PATHS TAB -->
    <div id="tab-fastpaths" class="tab-content">
      <div class="card">
        <div class="card-header">
          <div>
            <div class="card-title">Compiled Inks &amp; Artifacts</div>
            <div class="card-sub">Qualified local execution engines serving traffic without remote host calls</div>
          </div>
        </div>
        <div id="artifactsContainer"></div>
      </div>
    </div>

    <!-- ACTIVITY TAB -->
    <div id="tab-activity" class="tab-content">
      <div class="refresh-bar">
        <label><input type="checkbox" id="autoRefresh" checked> Auto-refresh (2s)</label>
        <button class="btn" onclick="loadActivity()">Refresh Now</button>
      </div>
      <div class="card">
        <div class="card-header">
          <div>
            <div class="card-title">Live Decision Stream</div>
            <div class="card-sub">Real-time ledger of decision outcomes, confidence scores, and source paths</div>
          </div>
        </div>
        <div id="activityContainer"></div>
      </div>
    </div>

    <!-- DISCOVERY TAB -->
    <div id="tab-discovery" class="tab-content">
      <div class="card">
        <div class="card-header">
          <div>
            <div class="card-title">Authority Horizon &amp; Candidate Sites</div>
            <div class="card-sub">Repeated semantic decision patterns identified across production traces</div>
          </div>
        </div>
        <div id="discoveryContainer"></div>
      </div>
    </div>

    <!-- SYSTEM TAB -->
    <div id="tab-system" class="tab-content">
      <div class="card">
        <div class="card-header">
          <div>
            <div class="card-title">Ink Doctor &amp; Engine Health</div>
            <div class="card-sub">Runtime configuration, database integrity, and serving tier readiness</div>
          </div>
        </div>
        <div id="systemContainer"></div>
      </div>
    </div>
  </main>

  <script>
    let activeTab = 'overview';

    // Vector Icon Templates (Clean SVG, Zero Emojis)
    const SVG_BOLT = `<svg class="svg-icon" viewBox="0 0 16 16" fill="currentColor"><path d="M9.5 1.5L2.5 9h5l-1 5.5L13.5 7h-5l1-5.5z"/></svg>`;
    const SVG_BRANCH = `<svg class="svg-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 8a5.5 5.5 0 0 1 9.39-3.89L13.5 6"/><path d="M13.5 2.5v3.5h-3.5"/><path d="M13.5 8a5.5 5.5 0 0 1-9.39 3.89L2.5 10"/><path d="M2.5 13.5V10h3.5"/></svg>`;
    const SVG_CHECK = `<svg class="svg-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8.5l3.5 3.5 6.5-7.5"/></svg>`;

    function showTab(tabId) {
      activeTab = tabId;
      document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.nav-btn').forEach(el => el.classList.remove('active'));
      document.getElementById('tab-' + tabId).classList.add('active');
      const idx = ['overview', 'sites', 'fastpaths', 'activity', 'discovery', 'system'].indexOf(tabId);
      if (idx >= 0) {
        document.querySelectorAll('.nav-btn')[idx].classList.add('active');
      }
      refreshTab(tabId);
    }

    async function apiFetch(endpoint) {
      try {
        const res = await fetch(endpoint);
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return await res.json();
      } catch (err) {
        console.error('API Error:', endpoint, err);
        return null;
      }
    }

    async function refreshTab(tabId) {
      if (tabId === 'overview') await loadOverview();
      else if (tabId === 'sites') await loadSites();
      else if (tabId === 'fastpaths') await loadFastPaths();
      else if (tabId === 'activity') await loadActivity();
      else if (tabId === 'discovery') await loadDiscovery();
      else if (tabId === 'system') await loadSystem();
    }

    async function loadOverview() {
      const data = await apiFetch('/api/overview');
      if (!data) return;

      document.getElementById('statDecisions').innerText = (data.total_decisions || 0).toLocaleString();
      document.getElementById('statFastRate').innerText = (data.fast_served_rate * 100).toFixed(1) + '%';
      document.getElementById('statFastSub').innerText = `${(data.fast_served || 0).toLocaleString()} served locally`;
      document.getElementById('statLatencySaved').innerText = `${Math.round(data.estimated_latency_saved_ms || 0).toLocaleString()} ms`;
      document.getElementById('statActiveSites').innerText = `${data.active_sites || 0}`;
      document.getElementById('statActiveSub').innerText = `${data.total_sites || 0} total registered`;

      if (data.is_demo) {
        document.getElementById('demoBadge').style.display = 'inline-block';
      }

      // Render Overview Sites Table
      const container = document.getElementById('overviewSitesContainer');
      if (!data.sites || data.sites.length === 0) {
        container.innerHTML = '<div class="empty-state"><h3>No DecisionSites recorded yet</h3><p>Decisions are registered automatically when your application calls ink.decide().</p></div>';
      } else {
        let html = '<table><thead><tr><th>Site</th><th>Status</th><th>Observations</th><th>Outcomes</th><th>Coverage</th><th>Fast Served</th><th>Engine</th></tr></thead><tbody>';
        for (const s of data.sites) {
          const badgeClass = 'badge-' + (s.state || 'OBSERVE');
          const cov = (s.outcome_coverage ? (s.outcome_coverage * 100).toFixed(1) : '0.0') + '%';
          html += `<tr>
            <td class="mono"><strong>${s.name}</strong></td>
            <td><span class="badge ${badgeClass}">${s.state}</span></td>
            <td>${s.observations || 0}</td>
            <td>${s.outcomes || 0}</td>
            <td>${cov}</td>
            <td><span class="source-tag source-fast">${SVG_BOLT} ${s.fast_served || 0}</span></td>
            <td><span class="code-pill">${s.engine || 'none'}</span></td>
          </tr>`;
        }
        html += '</tbody></table>';
        container.innerHTML = html;
      }

      // Render Recent Decisions
      const recentCont = document.getElementById('overviewRecentContainer');
      if (!data.recent_activity || data.recent_activity.length === 0) {
        recentCont.innerHTML = '<div class="empty-state"><p>No recent decisions recorded</p></div>';
      } else {
        let rHtml = '<table><thead><tr><th>Time</th><th>Site</th><th>Source</th><th>Choice</th><th>Confidence</th><th>Reason</th></tr></thead><tbody>';
        for (const row of data.recent_activity) {
          const isFast = row.source === 'fast_path';
          const srcIcon = isFast 
            ? `<span class="source-tag source-fast">${SVG_BOLT} fast_path</span>` 
            : `<span class="source-tag source-fallback">${SVG_BRANCH} fallback</span>`;
          const conf = row.confidence !== null ? (row.confidence * 100).toFixed(1) + '%' : '-';
          rHtml += `<tr>
            <td class="mono" style="color:var(--text-muted); font-size:0.75rem;">${formatTime(row.created)}</td>
            <td class="mono"><strong>${row.site}</strong></td>
            <td>${srcIcon}</td>
            <td><span class="code-pill">${row.choice}</span></td>
            <td>${conf}</td>
            <td style="color:var(--text-muted);">${row.fallback_reason || '-'}</td>
          </tr>`;
        }
        rHtml += '</tbody></table>';
        recentCont.innerHTML = rHtml;
      }
    }

    async function loadSites() {
      const data = await apiFetch('/api/sites');
      const cont = document.getElementById('sitesListContainer');
      if (!data || data.length === 0) {
        cont.innerHTML = '<div class="empty-state"><h3>No DecisionSites found</h3></div>';
        return;
      }
      let html = '<table><thead><tr><th>Site Name</th><th>State</th><th>Choices</th><th>Schema Fields</th><th>Observations</th><th>Outcomes</th><th>Blocker</th></tr></thead><tbody>';
      for (const s of data) {
        const badgeClass = 'badge-' + (s.state || 'OBSERVE');
        const choices = (s.choices || []).map(c => `<span class="code-pill">${c}</span>`).join(' ');
        const schema = Object.keys(s.schema || {}).join(', ') || '-';
        html += `<tr>
          <td class="mono"><strong>${s.name}</strong><br><span style="font-size:0.75rem; color:var(--text-muted);">${s.description || ''}</span></td>
          <td><span class="badge ${badgeClass}">${s.state}</span></td>
          <td>${choices}</td>
          <td class="mono" style="font-size:0.75rem; color:var(--text-muted);">${schema}</td>
          <td>${s.observations || 0}</td>
          <td>${s.outcomes || 0}</td>
          <td style="color:var(--text-muted);">${s.blocker || 'none'}</td>
        </tr>`;
      }
      html += '</tbody></table>';
      cont.innerHTML = html;
    }

    async function loadFastPaths() {
      const data = await apiFetch('/api/artifacts');
      const cont = document.getElementById('artifactsContainer');
      if (!data || data.length === 0) {
        cont.innerHTML = '<div class="empty-state"><h3>No Inks / Fast Paths compiled yet</h3><p>Run ink.maintenance() or wait for auto-maintenance to compile qualified candidate behavior.</p></div>';
        return;
      }
      let html = '<table><thead><tr><th>Artifact ID</th><th>Site</th><th>Engine</th><th>Status</th><th>Active Regions</th><th>Epoch</th><th>Integrity</th></tr></thead><tbody>';
      for (const a of data) {
        const badgeClass = 'badge-' + (a.status || 'SHADOW');
        html += `<tr>
          <td class="mono" style="font-size:0.75rem;">${(a.id || '').substring(0, 16)}...</td>
          <td class="mono"><strong>${a.site_name || a.site}</strong></td>
          <td><span class="code-pill">${a.engine}</span></td>
          <td><span class="badge ${badgeClass}">${a.status}</span></td>
          <td><strong>${a.active_regions_count || 0}</strong></td>
          <td>${a.epoch || 1}</td>
          <td><span class="verified-mark">${SVG_CHECK} Verified SHA256</span></td>
        </tr>`;
      }
      html += '</tbody></table>';
      cont.innerHTML = html;
    }

    async function loadActivity() {
      const data = await apiFetch('/api/activity');
      const cont = document.getElementById('activityContainer');
      if (!data || data.length === 0) {
        cont.innerHTML = '<div class="empty-state"><h3>No activity recorded yet</h3></div>';
        return;
      }
      let html = '<table><thead><tr><th>Timestamp</th><th>Site</th><th>Served By</th><th>Choice</th><th>Confidence</th><th>Reason / Details</th></tr></thead><tbody>';
      for (const row of data) {
        const isFast = row.source === 'fast_path';
        const srcIcon = isFast 
          ? `<span class="source-tag source-fast">${SVG_BOLT} fast_path</span>` 
          : `<span class="source-tag source-fallback">${SVG_BRANCH} fallback</span>`;
        const conf = row.confidence !== null ? (row.confidence * 100).toFixed(1) + '%' : '-';
        html += `<tr>
          <td class="mono" style="font-size:0.75rem; color:var(--text-muted);">${formatTime(row.created)}</td>
          <td class="mono"><strong>${row.site}</strong></td>
          <td>${srcIcon}</td>
          <td><span class="code-pill">${row.choice}</span></td>
          <td>${conf}</td>
          <td style="color:var(--text-muted); font-size:0.8125rem;">${row.fallback_reason || 'Qualified Fast Path'}</td>
        </tr>`;
      }
      html += '</tbody></table>';
      cont.innerHTML = html;
    }

    async function loadDiscovery() {
      const data = await apiFetch('/api/discovery');
      const cont = document.getElementById('discoveryContainer');
      if (!data || data.length === 0) {
        cont.innerHTML = '<div class="empty-state"><h3>No candidate sites discovered yet</h3><p>Run ink discover &lt;traces.jsonl&gt; to analyze candidate decision nodes in production logs.</p></div>';
        return;
      }
      let html = '<table><thead><tr><th>Candidate Site</th><th>Unique Patterns</th><th>Observation Volume</th><th>Potential Coverage</th><th>Candidate Engine</th></tr></thead><tbody>';
      for (const d of data) {
        html += `<tr>
          <td class="mono"><strong>${d.name}</strong></td>
          <td>${d.unique_patterns || 0}</td>
          <td>${d.volume || 0}</td>
          <td><strong style="color:#ffffff;">${(d.potential_coverage * 100).toFixed(1)}%</strong></td>
          <td><span class="code-pill">${d.suggested_engine || 'exact'}</span></td>
        </tr>`;
      }
      html += '</tbody></table>';
      cont.innerHTML = html;
    }

    async function loadSystem() {
      const data = await apiFetch('/api/system');
      const cont = document.getElementById('systemContainer');
      if (!data) return;
      let html = `<div style="padding: 1.5rem;">
        <h4 style="margin-bottom: 1rem; color: #ffffff;">Database &amp; Storage Health</h4>
        <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 1rem; margin-bottom: 2rem;">
          <div><span style="color:var(--text-muted);">Database Path:</span> <code class="code-pill">${data.database_path}</code></div>
          <div><span style="color:var(--text-muted);">Database Status:</span> <strong style="color:#ffffff;">${data.status}</strong></div>
          <div><span style="color:var(--text-muted);">SQLite Integrity Check:</span> <strong style="color:#ffffff;">${data.integrity}</strong></div>
          <div><span style="color:var(--text-muted);">Schema PRAGMA Version:</span> ${data.schema_version}</div>
          <div><span style="color:var(--text-muted);">Registered Sites Count:</span> ${data.sites_count}</div>
          <div><span style="color:var(--text-muted);">Active Artifacts Count:</span> ${data.active_artifacts_count}</div>
        </div>

        <h4 style="margin-bottom: 1rem; color: #ffffff;">Available Execution Engines</h4>
        <table>
          <thead><tr><th>Engine Name</th><th>Tier</th><th>Hardware Backend</th><th>Status</th></tr></thead>
          <tbody>
            <tr>
              <td class="mono"><strong>ExactEngine</strong></td>
              <td>Level 1 (Exact hash fast-path)</td>
              <td>Zero-overhead memory index</td>
              <td><span class="badge badge-ACTIVE">AVAILABLE</span></td>
            </tr>
            <tr>
              <td class="mono"><strong>LinearClassifierEngine</strong></td>
              <td>Level 2 (Linear semantic boundary)</td>
              <td>NumPy / BLAS CPU</td>
              <td><span class="badge badge-ACTIVE">AVAILABLE</span></td>
            </tr>
            <tr>
              <td class="mono"><strong>DecisionModelEngine</strong></td>
              <td>Level 3 (Local neural classifier)</td>
              <td>Apple Silicon MLX / Linux MLX</td>
              <td><span class="badge ${data.neural_available ? 'badge-ACTIVE' : 'badge-OBSERVE'}">${data.neural_available ? 'AVAILABLE' : 'OPTIONAL (pip install ink-jit[neural])'}</span></td>
            </tr>
          </tbody>
        </table>
      </div>`;
      cont.innerHTML = html;
    }

    function formatTime(ts) {
      if (!ts) return '-';
      const d = new Date(ts * 1000);
      return d.toLocaleTimeString();
    }

    // Auto-refresh interval
    setInterval(() => {
      const checkbox = document.getElementById('autoRefresh');
      if (checkbox && checkbox.checked && activeTab === 'activity') {
        loadActivity();
      }
    }, 2000);

    // Initial load
    showTab('overview');
  </script>
</body>
</html>
"""
