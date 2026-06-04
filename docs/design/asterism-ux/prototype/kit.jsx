// kit.jsx — shared design tokens, icons, and primitives for the Asterism UX
// exploration. Three visual DIRECTIONS share one information architecture; only
// the theme object (colors / type / shape / signature flags) changes.
//
// All styling is inline + theme-driven (no global `styles` object — that would
// collide across babel files). Components are pushed to window at the bottom.

// ── Three directions ────────────────────────────────────────────────────────
// entity (green) / activity (blue) follow PROV-DM and stay semantic across all.

const FOREST = {
  variant: 'forest',
  name: '森 · Forest',
  tagline: '今の forest green を洗練',
  bg: '#f3f6f0',
  surface: '#ffffff',
  surfaceAlt: '#f7faf4',
  fg: '#1b2a1d',
  muted: '#5f7263',
  faint: '#90a392',
  border: '#dde6dc',
  borderStrong: '#cad7c8',
  primary: '#3f6f49',
  primaryFg: '#ffffff',
  primarySoft: '#e7f0e6',
  accent: '#c08b3e', // warm amber for highlights
  accentSoft: '#f6ecd8',
  // dark "sky" zone (used sparingly, e.g. provenance rail)
  sky: '#1c2a1f',
  skyFg: '#eef4ec',
  skyMuted: '#9db3a0',
  skyBorder: 'rgba(255,255,255,0.10)',
  entity: '#3f7a4e',
  entitySoft: '#e6f0e6',
  activity: '#3d6f96',
  activitySoft: '#e4eef5',
  fontUI: '"Hanken Grotesk", "Zen Kaku Gothic New", "Noto Sans JP", system-ui, sans-serif',
  fontDisplay: '"Hanken Grotesk", "Zen Kaku Gothic New", "Noto Sans JP", system-ui, sans-serif',
  fontMono: '"IBM Plex Mono", monospace',
  radius: 13,
  radiusSm: 8,
  radiusLg: 18,
  shadow: '0 1px 2px rgba(20,35,22,0.05), 0 8px 24px rgba(20,35,22,0.06)',
  shadowSoft: '0 1px 2px rgba(20,35,22,0.04)',
  navStyle: 'rail',
  display: { weight: 700, spacing: '-0.02em' },
};

const CONSTELLATION = {
  variant: 'constellation',
  name: '星座 · Constellation',
  tagline: '「星をつなぐ」ブランドの世界観',
  bg: '#f4f5f8',
  surface: '#ffffff',
  surfaceAlt: '#f6f7fb',
  fg: '#16192a',
  muted: '#5a6076',
  faint: '#9197ad',
  border: '#e2e4ee',
  borderStrong: '#d2d5e4',
  primary: '#2c3566',
  primaryFg: '#ffffff',
  primarySoft: '#e7e9f5',
  accent: '#caa24a', // starlight gold
  accentSoft: '#f6eed7',
  sky: '#0f1330', // deep night sky
  skyFg: '#eef0fb',
  skyMuted: '#8e95bf',
  skyBorder: 'rgba(255,255,255,0.12)',
  entity: '#3f8a63',
  entitySoft: '#e3f0e9',
  activity: '#4a6ab0',
  activitySoft: '#e6ebf7',
  fontUI: '"Hanken Grotesk", "Noto Sans JP", system-ui, sans-serif',
  fontDisplay: '"Newsreader", "Noto Serif JP", Georgia, serif',
  fontMono: '"IBM Plex Mono", monospace',
  radius: 12,
  radiusSm: 7,
  radiusLg: 16,
  shadow: '0 1px 2px rgba(15,19,48,0.06), 0 10px 30px rgba(15,19,48,0.08)',
  shadowSoft: '0 1px 2px rgba(15,19,48,0.05)',
  navStyle: 'darkrail',
  display: { weight: 500, spacing: '-0.01em' },
};

const CLEAN = {
  variant: 'clean',
  name: '中立 · Clean',
  tagline: 'クリーンな白基調・大きな余白',
  bg: '#f4f4f2',
  surface: '#ffffff',
  surfaceAlt: '#fafafa',
  fg: '#17181a',
  muted: '#6a6c70',
  faint: '#9a9ca0',
  border: '#e7e7e4',
  borderStrong: '#dadad6',
  primary: '#1a1b1d',
  primaryFg: '#ffffff',
  primarySoft: '#efefec',
  accent: '#b5502f', // single terracotta accent
  accentSoft: '#f7eae4',
  sky: '#1a1b1d',
  skyFg: '#f4f4f2',
  skyMuted: '#9a9ca0',
  skyBorder: 'rgba(255,255,255,0.12)',
  entity: '#2f7d52',
  entitySoft: '#e8f1ec',
  activity: '#356797',
  activitySoft: '#e8eef4',
  fontUI: '"Hanken Grotesk", "Zen Kaku Gothic New", "Noto Sans JP", system-ui, sans-serif',
  fontDisplay: '"Hanken Grotesk", "Zen Kaku Gothic New", "Noto Sans JP", system-ui, sans-serif',
  fontMono: '"IBM Plex Mono", monospace',
  radius: 7,
  radiusSm: 5,
  radiusLg: 10,
  shadow: '0 1px 2px rgba(0,0,0,0.04), 0 6px 20px rgba(0,0,0,0.05)',
  shadowSoft: '0 1px 2px rgba(0,0,0,0.03)',
  navStyle: 'minimal',
  display: { weight: 700, spacing: '-0.03em' },
};

const THEMES = { forest: FOREST, constellation: CONSTELLATION, clean: CLEAN };

// ── Icons (simple stroke set; inherit currentColor) ─────────────────────────
function Icon({ d, size = 18, sw = 1.7, fill = 'none', children }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={fill} stroke="currentColor"
      strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round" style={{ flex: '0 0 auto' }}>
      {children || <path d={d} />}
    </svg>
  );
}
const Icons = {
  home: (p) => <Icon {...p}><path d="M4 11l8-7 8 7" /><path d="M6 10v9h12v-9" /></Icon>,
  add: (p) => <Icon {...p}><path d="M12 5v14M5 12h14" /></Icon>,
  ask: (p) => <Icon {...p}><path d="M21 11.5a8.4 8.4 0 0 1-12.4 7.4L3 20.5l1.5-4.4A8.4 8.4 0 1 1 21 11.5z" /></Icon>,
  catalog: (p) => <Icon {...p}><rect x="3.5" y="3.5" width="7" height="7" rx="1.5" /><rect x="13.5" y="3.5" width="7" height="7" rx="1.5" /><rect x="3.5" y="13.5" width="7" height="7" rx="1.5" /><rect x="13.5" y="13.5" width="7" height="7" rx="1.5" /></Icon>,
  activity: (p) => <Icon {...p}><path d="M3 3v5h5" /><path d="M3.5 11a9 9 0 1 1 .5 4" /><path d="M12 7v5l3 2" /></Icon>,
  code: (p) => <Icon {...p}><path d="M8 6l-5 6 5 6M16 6l5 6-5 6" /></Icon>,
  spark: (p) => <Icon {...p}><path d="M12 3l1.7 4.9L18.6 9.6 13.7 11.4 12 16.3 10.3 11.4 5.4 9.6 10.3 7.9z" /><path d="M19 15l.6 1.8 1.8.6-1.8.6-.6 1.8-.6-1.8-1.8-.6 1.8-.6z" /></Icon>,
  check: (p) => <Icon {...p}><path d="M4 12l5 5L20 6" /></Icon>,
  arrow: (p) => <Icon {...p}><path d="M5 12h14M13 6l6 6-6 6" /></Icon>,
  chevron: (p) => <Icon {...p}><path d="M9 6l6 6-6 6" /></Icon>,
  upload: (p) => <Icon {...p}><path d="M12 16V4M7 9l5-5 5 5" /><path d="M5 20h14" /></Icon>,
  file: (p) => <Icon {...p}><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /></Icon>,
  search: (p) => <Icon {...p}><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></Icon>,
  trace: (p) => <Icon {...p}><circle cx="6" cy="6" r="2.4" /><circle cx="18" cy="18" r="2.4" /><path d="M8 8l8 8" /></Icon>,
  doc: (p) => <Icon {...p}><path d="M6 3h9l4 4v14H6z" /><path d="M9 13h7M9 17h7M9 9h3" /></Icon>,
  layers: (p) => <Icon {...p}><path d="M12 3l9 5-9 5-9-5z" /><path d="M3 13l9 5 9-5" /></Icon>,
  link: (p) => <Icon {...p}><path d="M9 15l6-6" /><path d="M11 6l1-1a4 4 0 0 1 6 6l-1 1" /><path d="M13 18l-1 1a4 4 0 0 1-6-6l1-1" /></Icon>,
  dot: (p) => <Icon {...p} fill="currentColor"><circle cx="12" cy="12" r="4" /></Icon>,
};

// ── Constellation motif: stars + connecting lines (simple circles/lines) ────
function Constellation({ w = 200, h = 90, color = '#caa24a', dim = 'rgba(255,255,255,0.25)', seed = 1 }) {
  // a small fixed figure so it reads as an intentional asterism, not noise
  const pts = [
    [18, 60], [54, 30], [92, 54], [128, 24], [150, 66], [184, 40],
  ].map(([x, y], i) => [x * (w / 200), y * (h / 90), i]);
  const lines = [[0, 1], [1, 2], [2, 3], [3, 4], [4, 5], [2, 4]];
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} fill="none" style={{ display: 'block' }}>
      {lines.map(([a, b], i) => (
        <line key={i} x1={pts[a][0]} y1={pts[a][1]} x2={pts[b][0]} y2={pts[b][1]} stroke={dim} strokeWidth="1" />
      ))}
      {pts.map(([x, y], i) => (
        <circle key={i} cx={x} cy={y} r={i % 3 === 0 ? 2.6 : 1.7} fill={i % 2 ? color : dim} />
      ))}
    </svg>
  );
}

// ── Striped image / diagram placeholder ─────────────────────────────────────
function Placeholder({ label, t, h = 120, mono = true }) {
  return (
    <div style={{
      height: h, borderRadius: t.radiusSm, border: `1px dashed ${t.borderStrong}`,
      background: `repeating-linear-gradient(135deg, ${t.surfaceAlt} 0 10px, transparent 10px 20px)`,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      color: t.faint, fontSize: 12, fontFamily: mono ? t.fontMono : t.fontUI, letterSpacing: '0.02em',
    }}>{label}</div>
  );
}

// ── A plain-language class diagram (boxes + connector lines) ─────────────────
// Represents the Mermaid TBox output, drawn as labeled boxes so a non-RDF
// reader can grok it. Pure layout boxes + simple lines.
function ClassDiagram({ t, compact = false }) {
  const box = (title, sub, accent) => (
    <div style={{
      background: t.surface, border: `1px solid ${t.border}`, borderTop: `3px solid ${accent}`,
      borderRadius: t.radiusSm, padding: compact ? '8px 10px' : '10px 12px', minWidth: 116,
      boxShadow: t.shadowSoft,
    }}>
      <div style={{ fontWeight: 700, fontSize: compact ? 12 : 13, color: t.fg }}>{title}</div>
      <div style={{ fontSize: 10.5, color: t.faint, marginTop: 2, fontFamily: t.fontMono }}>{sub}</div>
    </div>
  );
  const lbl = (text) => (
    <span style={{ fontSize: 10, color: t.muted, fontFamily: t.fontMono, background: t.surfaceAlt, padding: '1px 6px', borderRadius: 20, border: `1px solid ${t.border}` }}>{text}</span>
  );
  return (
    <div style={{ position: 'relative', display: 'flex', flexDirection: 'column', gap: compact ? 14 : 20, alignItems: 'center', padding: '4px 0' }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        {box('論文', 'Paper', t.entity)}
        {lbl('fromPaper')}
        {box('試料', 'Sample', t.entity)}
      </div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        {box('測定曲線', 'Curve', t.entity)}
        {lbl('ofSample ↑')}
        {box('記述子', 'Descriptor', t.entity)}
      </div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        {lbl('wasGeneratedBy →')}
        {box('取り込み記録', 'IngestionActivity', t.activity)}
      </div>
    </div>
  );
}

Object.assign(window, { THEMES, FOREST, CONSTELLATION, CLEAN, Icon, Icons, Constellation, Placeholder, ClassDiagram });
