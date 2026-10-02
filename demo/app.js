/**
 * Microloop Sales Demonstration App
 * Renders technical live decision streams, counters, drift deopt, and frontier charts.
 */

let demoData = null;
let currentStep = 0;
let isPlaying = false;
let playTimer = null;
let autoMode = true;
let frontierDenominator = 'whole_app'; // 'whole_app' or 'site'

// Stream Counters
let totalStreamDecisions = 0;
let modelCallsWithoutMl = 0;
let modelCallsWithMl = 0;
let fastPathServes = 0;
let semanticCacheErrors = 0;
let microloopErrors = 0;

async function init() {
  if (window.MICROLOOP_DEMO_DATA) {
    demoData = window.MICROLOOP_DEMO_DATA;
  } else {
    try {
      const res = await fetch('data.json');
      demoData = await res.json();
    } catch (err) {
      console.warn("Direct fetch failed", err);
    }
  }

  setupEventListeners();
  renderFrontierChart();
  resetSimulation();
  
  // Start auto mode by default
  startAutoPlay();
}

function setupEventListeners() {
  document.getElementById('btn-play-pause')?.addEventListener('click', togglePlay);
  document.getElementById('btn-step')?.addEventListener('click', stepForward);
  document.getElementById('btn-drift')?.addEventListener('click', triggerDriftNow);
  document.getElementById('btn-reset')?.addEventListener('click', resetSimulation);
  
  document.getElementById('toggle-whole-app')?.addEventListener('click', () => {
    frontierDenominator = 'whole_app';
    document.getElementById('toggle-whole-app').classList.add('btn-primary');
    document.getElementById('toggle-whole-app').classList.remove('btn-secondary');
    document.getElementById('toggle-site').classList.remove('btn-primary');
    document.getElementById('toggle-site').classList.add('btn-secondary');
    renderFrontierChart();
  });

  document.getElementById('toggle-site')?.addEventListener('click', () => {
    frontierDenominator = 'site';
    document.getElementById('toggle-site').classList.add('btn-primary');
    document.getElementById('toggle-site').classList.remove('btn-secondary');
    document.getElementById('toggle-whole-app').classList.remove('btn-primary');
    document.getElementById('toggle-whole-app').classList.add('btn-secondary');
    renderFrontierChart();
  });
}

function resetSimulation() {
  pausePlay();
  currentStep = 0;
  totalStreamDecisions = 0;
  modelCallsWithoutMl = 0;
  modelCallsWithMl = 0;
  fastPathServes = 0;
  semanticCacheErrors = 0;
  microloopErrors = 0;

  const streamEl = document.getElementById('stream-rows');
  if (streamEl) streamEl.innerHTML = '';

  const bannerEl = document.getElementById('policy-banner');
  if (bannerEl) bannerEl.classList.remove('active');

  updateStatusBadge('OBSERVE', 'observe');
  updateCounters();
}

function togglePlay() {
  if (isPlaying) {
    pausePlay();
  } else {
    startAutoPlay();
  }
}

function startAutoPlay() {
  if (!demoData || !demoData.stream) return;
  isPlaying = true;
  const btn = document.getElementById('btn-play-pause');
  if (btn) btn.innerHTML = '⏸ Pause';

  if (playTimer) clearInterval(playTimer);
  playTimer = setInterval(() => {
    if (currentStep >= demoData.stream.length) {
      pausePlay();
      return;
    }
    stepForward();
  }, 1800); // ~1.8s per step gives an engaging ~65s demo
}

function pausePlay() {
  isPlaying = false;
  const btn = document.getElementById('btn-play-pause');
  if (btn) btn.innerHTML = '▶ Play Auto';
  if (playTimer) {
    clearInterval(playTimer);
    playTimer = null;
  }
}

function stepForward() {
  if (!demoData || !demoData.stream || currentStep >= demoData.stream.length) return;

  const item = demoData.stream[currentStep];
  currentStep++;

  // Update counters
  totalStreamDecisions++;
  modelCallsWithoutMl++;

  if (item.is_fast_path) {
    fastPathServes++;
  } else {
    modelCallsWithMl++;
  }

  if (item.cache_correct === false) {
    semanticCacheErrors++;
  }
  if (item.is_error) {
    microloopErrors++;
  }

  // Check state and drift trigger
  if (item.phase === 'DRIFT' && item.microloop_state.includes('DEOPT')) {
    const banner = document.getElementById('policy-banner');
    if (banner && !banner.classList.contains('active')) {
      banner.classList.add('active');
    }
    updateStatusBadge('SHADOW (DEOPT RECOVERY)', 'drift');
  } else if (item.phase === 'DRIFT') {
    updateStatusBadge(item.microloop_state, 'drift');
  } else if (item.microloop_state === 'ACTIVE') {
    updateStatusBadge('ACTIVE (LOCAL FAST PATH)', 'active');
  } else if (item.microloop_state === 'SHADOW') {
    updateStatusBadge('SHADOW (QUALIFYING)', 'shadow');
  } else {
    updateStatusBadge('OBSERVE (LEARNING)', 'observe');
  }

  // Render row
  renderStreamRow(item);
  updateCounters();
}

function triggerDriftNow() {
  if (!demoData || !demoData.stream) return;
  // Jump directly to the first drift item
  const driftIdx = demoData.stream.findIndex(s => s.phase === 'DRIFT');
  if (driftIdx >= 0) {
    currentStep = driftIdx;
    const banner = document.getElementById('policy-banner');
    if (banner) banner.classList.add('active');
    stepForward();
  }
}

function updateStatusBadge(text, stateClass) {
  const badgeText = document.getElementById('lifecycle-text');
  const dot = document.getElementById('lifecycle-dot');
  if (badgeText) badgeText.innerText = text;
  if (dot) {
    dot.className = 'status-dot';
    if (stateClass === 'active') dot.classList.add('active');
    else if (stateClass === 'drift') dot.classList.add('drift');
    else if (stateClass === 'shadow') dot.classList.add('shadow');
  }
}

function renderStreamRow(item) {
  const container = document.getElementById('stream-rows');
  if (!container) return;

  const row = document.createElement('div');
  row.className = 'stream-row';
  if (item.is_fast_path) row.classList.add('highlight-fp');
  if (item.is_error || item.cache_correct === false) row.classList.add('highlight-drift');

  let sourceClass = 'model';
  if (item.is_fast_path) sourceClass = 'fast-path';
  else if (item.source.includes('DEOPT')) sourceClass = 'deopt';

  const latText = item.latency_ms < 1.0 ? `${item.latency_ms.toFixed(2)} ms` : `${item.latency_ms.toFixed(0)} ms`;
  const latClass = item.latency_ms < 1.0 ? 'fast' : '';

  row.innerHTML = `
    <div class="stream-row-left">
      <span class="ticket-id">${item.id}</span>
      <span class="ticket-text" title="${item.text}">${item.text}</span>
    </div>
    <div class="stream-row-right">
      <span class="badge-choice">${item.choice}</span>
      <span class="badge-source ${sourceClass}">${item.source}</span>
      <span class="stream-lat ${latClass}">${latText}</span>
    </div>
  `;

  container.insertBefore(row, container.firstChild);
}

function updateCounters() {
  const countNoMl = document.getElementById('count-no-ml');
  const countWithMl = document.getElementById('count-with-ml');
  const countFp = document.getElementById('count-fp');
  const fillModel = document.getElementById('fill-model');
  const fillFp = document.getElementById('fill-fp');

  if (countNoMl) countNoMl.innerText = `${modelCallsWithoutMl} calls`;
  if (countWithMl) countWithMl.innerText = `${modelCallsWithMl}`;
  if (countFp) countFp.innerText = `${fastPathServes}`;

  const total = Math.max(1, modelCallsWithMl + fastPathServes);
  const fpPct = ((fastPathServes / total) * 100).toFixed(0);
  const modelPct = 100 - fpPct;

  if (fillModel) fillModel.style.width = `${modelPct}%`;
  if (fillFp) fillFp.style.width = `${fpPct}%`;

  // Update Drift summary
  const scErrEl = document.getElementById('sc-drift-errors');
  const mlErrEl = document.getElementById('ml-drift-errors');
  if (scErrEl) scErrEl.innerText = `${semanticCacheErrors}`;
  if (mlErrEl) mlErrEl.innerText = `${microloopErrors}`;
}

// ---------------------------------------------------------------------------
// Frontier Chart Rendering (SVG)
// ---------------------------------------------------------------------------

function renderFrontierChart() {
  const container = document.getElementById('frontier-chart-container');
  if (!container || !demoData || !demoData.frontier) return;

  const w = container.clientWidth || 740;
  const h = 340;
  const pad = { top: 30, right: 180, bottom: 45, left: 55 };
  const plotW = w - pad.left - pad.right;
  const plotH = h - pad.top - pad.bottom;

  // Max bounds
  const maxX = frontierDenominator === 'whole_app' ? 25.0 : 100.0;
  const maxY = 22.0;

  const toX = val => pad.left + (val / maxX) * plotW;
  const toY = val => pad.top + plotH - (val / maxY) * plotH;

  const arms = demoData.frontier;
  const colorMap = {
    original_model: '#94a3b8',
    exact_cache: '#64748b',
    semantic_cache: '#f43f5e',
    cheap_model: '#f59e0b',
    supervised_classifier: '#3b82f6',
    microloop: '#10b981',
  };
  const labelMap = {
    original_model: 'Original Model',
    exact_cache: 'Exact Cache',
    semantic_cache: 'Semantic Cache (stale errors)',
    cheap_model: 'Cheap Model Tier',
    supervised_classifier: 'Supervised Classifier',
    microloop: 'Microloop Decision JIT',
  };

  let svg = `<svg viewBox="0 0 ${w} ${h}" width="100%" height="100%" style="font-family: inherit;">`;

  // Grid lines
  for (let y = 0; y <= maxY; y += 5) {
    const yPos = toY(y);
    svg += `<line x1="${pad.left}" y1="${yPos}" x2="${pad.left + plotW}" y2="${yPos}" stroke="#222d44" stroke-width="1" />`;
    svg += `<text x="${pad.left - 10}" y="${yPos + 4}" fill="#64748b" font-size="11" text-anchor="end">${y}%</text>`;
  }

  const xSteps = frontierDenominator === 'whole_app' ? 5 : 20;
  for (let x = 0; x <= maxX; x += xSteps) {
    const xPos = toX(x);
    svg += `<line x1="${xPos}" y1="${pad.top}" x2="${xPos}" y2="${pad.top + plotH}" stroke="#222d44" stroke-width="1" />`;
    svg += `<text x="${xPos}" y="${pad.top + plotH + 18}" fill="#64748b" font-size="11" text-anchor="middle">${x}%</text>`;
  }

  // Axis Labels
  const xLabel = frontierDenominator === 'whole_app' 
    ? 'Whole-Application Model Calls Avoided % (Total Fleet Denominator)' 
    : 'DecisionSite Calls Avoided % (Site Denominator)';
  svg += `<text x="${pad.left + plotW / 2}" y="${h - 8}" fill="#94a3b8" font-size="12" font-weight="600" text-anchor="middle">${xLabel}</text>`;
  svg += `<text x="16" y="${pad.top + plotH / 2}" fill="#94a3b8" font-size="12" font-weight="600" text-anchor="middle" transform="rotate(-90 16 ${pad.top + plotH / 2})">Wrong-Serve Rate %</text>`;

  // Plot Lines & Points
  for (const [armKey, points] of Object.entries(arms)) {
    if (!points || points.length === 0) continue;
    const color = colorMap[armKey] || '#ffffff';

    // Generate path
    const validPoints = points.map(p => ({
      x: frontierDenominator === 'whole_app' ? p.whole_app_reduction_pct : p.site_reduction_pct,
      y: p.wrong_serve_rate_pct,
      is_default: p.is_default,
      wrong_serves: p.wrong_serves,
      config: p.config,
    }));

    if (validPoints.length > 1) {
      let d = `M ${toX(validPoints[0].x)} ${toY(validPoints[0].y)}`;
      for (let i = 1; i < validPoints.length; i++) {
        d += ` L ${toX(validPoints[i].x)} ${toY(validPoints[i].y)}`;
      }
      svg += `<path d="${d}" fill="none" stroke="${color}" stroke-width="${armKey === 'microloop' ? '2.5' : '1.5'}" opacity="0.85" />`;
    }

    // Points
    validPoints.forEach(p => {
      const cx = toX(p.x);
      const cy = toY(p.y);
      if (p.is_default) {
        // Highlighting Microloop default operating point
        svg += `<circle cx="${cx}" cy="${cy}" r="7" fill="${color}" stroke="#ffffff" stroke-width="2" />`;
        svg += `<text x="${cx + 10}" y="${cy - 8}" fill="#34d399" font-size="11" font-weight="700">Microloop (Demotes in 8 calls)</text>`;
      } else {
        svg += `<circle cx="${cx}" cy="${cy}" r="4" fill="${color}" opacity="0.9" />`;
      }
    });
  }

  // Legend
  const legendX = pad.left + plotW + 16;
  let legendY = pad.top + 10;
  svg += `<text x="${legendX}" y="${legendY}" fill="#ffffff" font-size="11" font-weight="700">STRATEGY ARMS</text>`;
  legendY += 16;

  for (const [armKey, label] of Object.entries(labelMap)) {
    const color = colorMap[armKey];
    svg += `<circle cx="${legendX + 5}" cy="${legendY}" r="4" fill="${color}" />`;
    svg += `<text x="${legendX + 16}" y="${legendY + 3.5}" fill="#cbd5e1" font-size="10.5">${label}</text>`;
    legendY += 18;
  }

  svg += `</svg>`;
  container.innerHTML = svg;
}

window.addEventListener('DOMContentLoaded', init);
window.addEventListener('resize', renderFrontierChart);
