// Asterism static demo — deterministic, server-less, AI-free.
//
// Loads a small starrydata RDF subset and runs the SAME SPARQL the production
// typed MCP tools run (property_ranking / sample_search / provenance_of /
// template_curve_fetch), in-browser via oxigraph-wasm. answers.json (built by
// scripts/build_demo_assets.py with those same tools) is the graceful fallback
// and the source of provenance chains + curve plots. Design:
// docs/architecture/static-citable-facts-demo.md
import init, { Store, namedNode } from './lib/oxigraph.js';

const SD = 'https://kumagallium.github.io/asterism/starrydata/ontology#';
const MP_GRAPH = 'https://kumagallium.github.io/asterism/starrydata/graph/mp-links';
const MP_BASE = 'https://next-gen.materialsproject.org/materials/';
const PROV_STEP_LABEL = {
  [SD + 'IngestionActivity']: 'ingestion',
  [SD + 'DigitizationActivity']: 'digitization',
};

const $ = (id) => document.getElementById(id);
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html != null) n.innerHTML = html;
  return n;
};
const esc = (s) =>
  String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
// Mirror asterism_mcp.tools._sparql_escape_literal for the live composition filter.
const sparqlEsc = (s) =>
  String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n').replace(/\r/g, '');

let DATA = null; // answers.json
let store = null; // oxigraph Store (null => fallback mode)
let LIVE = false;

// --- engine ---------------------------------------------------------------

function setEngine(state, text) {
  const pill = $('engine-pill');
  pill.className = 'engine-pill ' + state;
  $('engine-text').textContent = text;
}

/** Run a SELECT live and return rows as plain {var: value} objects, or null. */
function runSelect(sparql) {
  if (!LIVE || !store) return null;
  const rows = [];
  for (const binding of store.query(sparql)) {
    const row = {};
    for (const [k, term] of binding) row[k] = term ? term.value : undefined;
    rows.push(row);
  }
  return rows;
}

// --- ranking (property_ranking) -------------------------------------------

function renderRanking() {
  const pre = DATA.ranking;
  const sparql = pre.sparql;
  $('ranking-sparql').textContent = sparql;

  let results;
  const live = runSelect(sparql);
  if (live) {
    results = live.map((r) => ({
      curve_iri: r.curve,
      value: r.ymax != null ? parseFloat(r.ymax) : null,
      sample_iri: r.s,
      composition: r.comp,
      paper_iri: r.p,
      title: r.title,
    }));
  } else {
    results = pre.result.results;
  }

  const body = $('ranking-body');
  body.className = '';
  body.innerHTML = '';
  const table = el('table', 'rank');
  table.innerHTML =
    '<thead><tr><th>#</th><th>ZT (yMax)</th><th>組成</th><th>論文</th></tr></thead>';
  const tb = el('tbody');
  results.forEach((r, i) => {
    const tr = el('tr');
    tr.dataset.iri = r.curve_iri;
    tr.innerHTML =
      `<td class="rank-no">${i + 1}</td>` +
      `<td class="rank-val">${r.value != null ? r.value.toFixed(3) : '—'}</td>` +
      `<td class="rank-comp">${esc(r.composition || '—')}</td>` +
      `<td class="rank-title">${esc(r.title || '—')}</td>`;
    tr.addEventListener('click', () => {
      tb.querySelectorAll('tr').forEach((x) => x.classList.remove('active'));
      tr.classList.add('active');
      showProvenance(r.curve_iri);
    });
    tb.appendChild(tr);
  });
  table.appendChild(tb);
  body.appendChild(table);

  const excluded = pre.result.excluded_implausible;
  const note = $('ranking-note');
  if (excluded > 0) {
    note.innerHTML = `<div class="note warn">データ品質: 物理的にあり得ない ZT（&gt; ${pre.result.max_plausible}）を <b>${excluded} 件</b>除外しました（デジタイズ時の軸ラベル誤りなど）。</div>`;
  } else {
    note.innerHTML = `<div class="note">データ品質: ZT &gt; ${pre.result.max_plausible} の除外は <b>0 件</b>（このサブセットの値はすべて物理的に妥当な範囲です）。</div>`;
  }

  // auto-open the top row's provenance
  if (results.length) {
    tb.firstChild.classList.add('active');
    showProvenance(results[0].curve_iri);
  }
}

// --- provenance (provenance_of) -------------------------------------------

const cell = (row, k) => (row && row[k] != null ? row[k] : null);

/** Mirror asterism_mcp.tools.provenance_of post-processing over raw bindings. */
function buildChain(rows, iri) {
  if (!rows || !rows.length) return { iri, found: false, chain: [] };
  const first = rows[0];
  const etype = cell(first, 'etype') || '';
  const chain = [];

  if (etype === SD + 'Curve' || cell(first, 'fig') || cell(first, 'py')) {
    const bits = [];
    if (cell(first, 'py')) bits.push(cell(first, 'py'));
    if (cell(first, 'ymax')) bits.push(`yMax=${cell(first, 'ymax')}`);
    chain.push({ step: 'curve', iri, label: cell(first, 'fig') || 'curve', detail: bits.join('; ') });
  }

  const isSample = etype === SD + 'Sample';
  const sampleIri = cell(first, 'sample') || (isSample ? iri : null);
  const sComp = cell(first, 'scomp') || (isSample ? cell(first, 'ecomp') : null);
  const sName = cell(first, 'sname') || (isSample ? cell(first, 'ename') : null);
  if (sampleIri) {
    chain.push({
      step: 'sample',
      iri: sampleIri,
      label: sComp || sName || 'sample',
      detail: sComp ? `composition=${sComp}` : '',
    });
  }

  const paperIri = cell(first, 'paper') || (etype === SD + 'Paper' ? iri : null);
  if (paperIri) {
    chain.push({
      step: 'paper',
      iri: paperIri,
      label: cell(first, 'ptitle') || 'paper',
      detail: cell(first, 'pid') ? `id=${cell(first, 'pid')}` : '',
    });
  }

  const seen = new Set();
  const acts = [];
  for (const r of rows) {
    const act = cell(r, 'act');
    if (!act || seen.has(act)) continue;
    seen.add(act);
    const atype = cell(r, 'atype') || '';
    acts.push({
      step: PROV_STEP_LABEL[atype] || 'activity',
      iri: act,
      label: atype ? atype.split('#').pop() : 'Activity',
      detail: cell(r, 'atime') ? `atTime=${cell(r, 'atime')}` : '',
    });
  }
  const order = { digitization: 0, ingestion: 1, activity: 2 };
  acts.sort((a, b) => (order[a.step] ?? 3) - (order[b.step] ?? 3));
  chain.push(...acts);

  return { iri, found: true, chain };
}

function showProvenance(iri) {
  const pre = DATA.provenance;
  const sparql = pre.sparql_template.replace('%IRI%', iri);
  $('prov-sparql').textContent = sparql;

  let result;
  const live = runSelect(sparql);
  if (live) {
    result = buildChain(live, iri);
  } else {
    result = pre.chains[iri] || { iri, found: false, chain: [] };
  }

  const body = $('prov-body');
  body.className = '';
  if (!result.found || !result.chain.length) {
    body.className = 'empty';
    body.textContent = '来歴が見つかりませんでした。';
    $('plot-body').innerHTML = '';
    return;
  }
  const trace = el('div', 'prov');
  result.chain.forEach((s) => {
    const isAct = s.step === 'ingestion' || s.step === 'digitization' || s.step === 'activity';
    const step = el('div', 'prov-step' + (isAct ? ' activity' : ''));
    const href = esc(s.iri);
    step.innerHTML =
      `<span class="dot"></span>` +
      `<div class="head"><span class="kind">${esc(s.step)}</span><span class="name">${esc(s.label)}</span></div>` +
      (s.detail ? `<div class="detail">${esc(s.detail)}</div>` : '') +
      `<div class="iri"><a href="${href}" target="_blank" rel="noopener">${href}</a></div>`;
    trace.appendChild(step);
  });
  body.innerHTML = '';
  body.appendChild(trace);

  renderPlot(iri);
}

// --- curve plot (template_curve_fetch, featured only) ---------------------

function renderPlot(curveIri) {
  const box = $('plot-body');
  const c = DATA.featured_curves[curveIri];
  if (!c || !c.x || !c.y || c.x.length < 2) {
    box.innerHTML = '<div class="empty">この曲線の点列はデモサブセットに含まれていません。</div>';
    return;
  }
  const W = 320,
    H = 220,
    pad = 34;
  const xs = c.x,
    ys = c.y;
  const xmin = Math.min(...xs),
    xmax = Math.max(...xs);
  const ymin = Math.min(...ys),
    ymax = Math.max(...ys);
  const sx = (v) => pad + ((v - xmin) / (xmax - xmin || 1)) * (W - pad - 8);
  const sy = (v) => H - pad - ((v - ymin) / (ymax - ymin || 1)) * (H - pad - 10);
  const pts = xs.map((x, i) => `${sx(x).toFixed(1)},${sy(ys[i]).toFixed(1)}`).join(' ');
  const xlab = `${esc(c.property_x || 'x')}${c.unit_x ? ' [' + esc(c.unit_x) + ']' : ''}`;
  const ylab = `${esc(c.property_y || 'y')}${c.unit_y && c.unit_y !== '-' ? ' [' + esc(c.unit_y) + ']' : ''}`;
  box.innerHTML =
    `<div class="plot-box"><div class="cap">${esc(c.figure_name || 'curve')} — ${ylab} vs ${xlab}</div>` +
    `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${ylab} vs ${xlab}">` +
    `<line x1="${pad}" y1="${H - pad}" x2="${W - 8}" y2="${H - pad}" stroke="#cad7c8"/>` +
    `<line x1="${pad}" y1="10" x2="${pad}" y2="${H - pad}" stroke="#cad7c8"/>` +
    `<text x="${pad}" y="${H - 8}" font-size="9" fill="#90a392">${xmin.toPrecision(3)}</text>` +
    `<text x="${W - 8}" y="${H - 8}" font-size="9" fill="#90a392" text-anchor="end">${xmax.toPrecision(3)}</text>` +
    `<text x="4" y="16" font-size="9" fill="#90a392">${ymax.toPrecision(3)}</text>` +
    `<text x="4" y="${H - pad}" font-size="9" fill="#90a392">${ymin.toPrecision(3)}</text>` +
    `<polyline fill="none" stroke="#3f6f49" stroke-width="1.8" points="${pts}"/>` +
    xs
      .map((x, i) => `<circle cx="${sx(x).toFixed(1)}" cy="${sy(ys[i]).toFixed(1)}" r="1.7" fill="#c26356"/>`)
      .join('') +
    `</svg><div class="cap" style="text-align:center">${xlab}</div></div>`;
}

// --- composition search (sample_search) -----------------------------------

function renderSearch(value) {
  const pre = DATA.composition;
  const q = (value ?? pre.default_value).trim();
  $('search-input').value = q;
  const sparql = pre.sparql_template.replace('%Q%', sparqlEsc(q.toLowerCase()));
  $('search-sparql').textContent = sparql;

  let results;
  const live = runSelect(sparql);
  if (live) {
    results = live.map((r) => ({
      sample_iri: r.sample,
      composition: r.comp,
      name: r.name,
      paper_iri: r.paper,
      title: r.title,
    }));
  } else if (q.toLowerCase() === pre.default_value.toLowerCase()) {
    results = pre.result.results;
  } else {
    const body = $('search-body');
    body.className = 'empty';
    body.textContent =
      'カスタム検索にはブラウザ内 SPARQL エンジンが必要です（読み込みに失敗）。既定の組成のみ表示できます。';
    return;
  }

  const body = $('search-body');
  body.className = '';
  if (!results.length) {
    body.className = 'empty';
    body.textContent = `「${q}」に一致するサンプルはありません。`;
    return;
  }
  const list = el('div', 'cites');
  results.forEach((r) => {
    const c = el('div', 'cite');
    c.innerHTML =
      `<span class="badge">sample</span>` +
      `<div class="label">${esc(r.composition || r.name || 'sample')}</div>` +
      (r.title ? `<div class="field"><b>paper</b> ${esc(r.title)}</div>` : '') +
      `<div class="field"><b>IRI</b> ${esc(r.sample_iri)}</div>`;
    list.appendChild(c);
  });
  body.innerHTML = `<div class="muted" style="font-size:.84rem;margin-bottom:.6rem">${results.length} 件</div>`;
  body.appendChild(list);
}

function setupSearchControls() {
  $('search-btn').addEventListener('click', () => renderSearch($('search-input').value));
  $('search-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') renderSearch($('search-input').value);
  });
  const chips = $('search-chips');
  ['Ba8Ga16', 'Ba8', 'Ga16', 'Ge', 'Zn'].forEach((c) => {
    const b = el('button', 'chip', esc(c));
    b.addEventListener('click', () => renderSearch(c));
    chips.appendChild(b);
  });
}

// --- cross-dataset: starrydata × Materials Project ------------------------

function renderCross() {
  const pre = DATA.cross;
  const card = $('cross-card');
  if (!pre || !pre.rows || !pre.rows.length) {
    if (card) card.style.display = 'none';
    return;
  }
  if (card) card.style.display = '';
  $('cross-sparql').textContent = pre.sparql;

  // bridge details are precomputed per host_formula
  const bridgeByHost = {};
  pre.rows.forEach((r) => (bridgeByHost[r.host_formula] = r));

  let rows;
  const live = runSelect(pre.sparql);
  if (live) {
    rows = live.map((r) => ({
      host_formula: r.formula,
      zt: r.ztmax != null ? parseFloat(r.ztmax) : null,
      n_samples: r.nsamples != null ? parseInt(r.nsamples, 10) : null,
      space_group: r.sg,
      crystal_system: r.csys,
      prototype: r.proto,
      mp_iri: r.mp,
      mp_id: r.mp ? r.mp.split('/').pop() : null,
    }));
  } else {
    rows = pre.rows;
  }

  const body = $('cross-body');
  body.className = '';
  body.innerHTML = '';
  const table = el('table', 'rank');
  table.innerHTML =
    '<thead><tr><th>母相</th><th>最大 ZT</th><th>試料数</th><th>空間群</th>' +
    '<th>結晶系</th><th>mp-id (MP)</th><th></th></tr></thead>';
  const tb = el('tbody');
  rows.forEach((r) => {
    const ref = bridgeByHost[r.host_formula] || r;
    const isDemo = !r.mp_id || r.mp_id.startsWith('mp-DEMO');
    const mpCell = r.mp_id
      ? isDemo
        ? `<span class="rank-comp muted">${esc(r.mp_id)}</span>`
        : `<a class="rank-comp" href="${MP_BASE}${esc(r.mp_id)}" target="_blank" rel="noopener">${esc(r.mp_id)} ↗</a>`
      : '—';
    const tr = el('tr');
    tr.innerHTML =
      `<td class="rank-comp">${esc(r.host_formula)}</td>` +
      `<td class="rank-val">${r.zt != null ? r.zt.toFixed(3) : '—'}</td>` +
      `<td class="rank-no">${r.n_samples ?? '—'}</td>` +
      `<td class="rank-comp">${esc(r.space_group || '—')}</td>` +
      `<td class="rank-title">${esc(r.crystal_system || '—')}</td>` +
      `<td>${mpCell}</td>` +
      `<td class="muted" style="font-size:.8rem">結合の仕組み ▸</td>`;
    const bridgeTr = el('tr', 'bridge-row');
    const td = el('td');
    td.colSpan = 7;
    td.appendChild(buildBridge(r, ref));
    bridgeTr.appendChild(td);
    bridgeTr.style.display = 'none';
    tr.addEventListener('click', () => {
      const open = bridgeTr.style.display !== 'none';
      bridgeTr.style.display = open ? 'none' : '';
      tr.classList.toggle('active', !open);
    });
    tb.appendChild(tr);
    tb.appendChild(bridgeTr);
  });
  table.appendChild(tb);
  body.appendChild(table);

  $('cross-note').innerHTML =
    `<div class="note">この結合は <b>2 つの別グラフ</b>（既定=starrydata / 名前付き=Materials Project）を ` +
    `<b>同じ <code>sample</code> IRI</b> で結合しています。データ取得元 = ${esc(pre.source)}。` +
    (pre.source && pre.source.startsWith('demo')
      ? ' <b>mp-id は実 ID ではなくプレースホルダ</b>です（MP_API_KEY で live 実行すると実 mp-id に解決）。'
      : '') +
    '</div>' +
    `<div class="muted" style="font-size:.8rem;margin-top:.5rem">` +
    `※ 同じ突き合わせはスクリプトでも書けます。違いは、結合を<b>使い捨てのコード</b>ではなく ` +
    `<b>型付き・来歴つきの再利用できるデータ</b>として残す点です（誰でも・他ツールも・3 つ目以降の ` +
    `データセットも同じ IRI で再利用でき、対応づけ自体を監査・引用できる）。` +
    `各母相は MP の<b>最安定相</b>（e_above_hull 最小の多形）を採用しています。` +
    `</div>`;
}

function buildBridge(row, ref) {
  const b = (ref && ref.bridge) || {};
  const host = esc(row.host_formula);
  const mpId = esc(row.mp_id || (ref && ref.mp_id) || '');
  const wrap = el('div', 'bridge');
  // 1) the predicate path (how it's connected ontologically)
  const path =
    `<div class="bridge-path">` +
    `<span class="node entity">sample</span>` +
    `<span class="edge">— sd:hasHostStructure →</span>` +
    `<span class="node entity">CrystalStructure<small>${host}</small></span>` +
    `<span class="edge">— sd:idealizedFrom →</span>` +
    `<span class="node mp">MP<small>${mpId}</small></span>` +
    `</div>`;
  // 2) the ontological semantics (the careful modeling)
  const tbox =
    `<div class="bridge-note"><b>オントロジー上の意味</b>: ` +
    `<code>sd:idealizedFrom</code> は <code>prov:wasDerivedFrom</code> のサブプロパティで、` +
    `<b>owl:sameAs ではありません</b>（ドープした実サンプル ≠ MP の純粋計算相＝「母相を参照」に留める）。` +
    `ドープは <code>sd:PointDefect</code>（点欠陥）として表現します。</div>`;
  // 3) provenance of the link itself (StructureMatchActivity)
  const dop =
    b.dopants && b.dopants.length
      ? b.dopants.map((d) => `${esc(d.element)}(${esc(d.amount)})`).join(', ')
      : 'なし';
  const act =
    `<div class="bridge-note"><b>リンクの来歴</b> (<code>sd:StructureMatchActivity</code>): ` +
    `方法=${esc(b.match_method || '—')} ／ 一致度=${esc(b.match_confidence || '—')}` +
    (b.match_time ? ` ／ 日時=${esc(b.match_time)}` : '') +
    ` ／ 点欠陥(ドープ)=${dop}</div>`;
  const iris =
    `<div class="bridge-iris">` +
    (b.structure_iri ? `<div><b>structure</b> ${esc(b.structure_iri)}</div>` : '') +
    (b.mp_iri ? `<div><b>MP</b> ${esc(b.mp_iri)}</div>` : '') +
    `</div>`;
  wrap.innerHTML = path + tbox + act + iris;
  return wrap;
}

// --- boot -----------------------------------------------------------------

async function boot() {
  setEngine('', 'データを読み込み中…');
  let ttlText = '';
  let mpTtl = '';
  try {
    // no-cache = always revalidate with the server (cheap 304 when unchanged),
    // so a rebuilt dataset is never masked by a stale browser copy.
    const opt = { cache: 'no-cache' };
    const [aRes, tRes, mRes] = await Promise.all([
      fetch('./data/answers.json', opt),
      fetch('./data/starrydata-demo.ttl', opt),
      fetch('./data/mp-links.ttl', opt).catch(() => null),
    ]);
    DATA = await aRes.json();
    ttlText = await tRes.text();
    if (mRes && mRes.ok) mpTtl = await mRes.text();
  } catch (e) {
    setEngine('fallback', 'データの読み込みに失敗しました');
    console.error(e);
    return;
  }

  const m = DATA.meta;
  $('meta-counts').textContent = `papers ${m.papers} / samples ${m.samples} / curves ${m.curves}`;
  if ($('meta-mp') && m.mp_linked_rows != null) {
    $('meta-mp').textContent = `${m.mp_linked_rows} 構造・mode=${m.mp_source || '?'}`;
  }

  setEngine('', 'SPARQL エンジン (wasm) を初期化中…');
  try {
    await init();
    store = new Store();
    store.load(ttlText, {
      format: 'text/turtle',
      base_iri: 'https://kumagallium.github.io/asterism/starrydata/resource/',
    });
    if (mpTtl) {
      store.load(mpTtl, { format: 'text/turtle', to_graph_name: namedNode(MP_GRAPH) });
    }
    LIVE = true;
    setEngine('live', 'ライブ: ブラウザ内 SPARQL を実行中 (oxigraph-wasm)');
  } catch (e) {
    LIVE = false;
    setEngine('fallback', '事前計算結果を表示中 (wasm 初期化に失敗 — 答えは同一)');
    console.warn('oxigraph-wasm unavailable, using precomputed answers', e);
  }

  setupSearchControls();
  renderRanking();
  renderSearch();
  renderCross();
}

boot();
