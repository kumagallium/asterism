// shell.jsx — the app frame (sidebar + topbar), theme-driven. Encodes the NEW
// information architecture: plain-language, verb-led nav that mirrors the user's
// mental model (入れる → 確かめる → 見渡す) instead of the pipeline's internals.

// New IA. Each item: plain Japanese label + small English tag. Specialist nouns
// (RDF / SPARQL) are demoted or tucked under "開発者向け".
const NAV = [
  { id: 'home', label: 'ホーム', en: 'Home', icon: 'home' },
  { id: 'add', label: 'データを追加', en: 'Add data', icon: 'add', group: 'つくる' },
  { id: 'ask', label: '質問する', en: 'Ask', icon: 'ask', group: 'つかう' },
  { id: 'catalog', label: 'カタログ', en: 'Catalog', icon: 'catalog', group: 'つかう' },
  { id: 'activity', label: 'アクティビティ', en: 'Activity', icon: 'activity', group: '管理' },
];

const TITLES = {
  home: { eyebrow: 'はじめに', title: 'ホーム', sub: '今ある「つながったデータ」と次の一手' },
  add: { eyebrow: 'つくる', title: 'データを追加', sub: 'CSV・API などのデータソースから、AI と一緒に知識グラフを作る' },
  ask: { eyebrow: 'つかう', title: '質問する', sub: '取り込んだデータに、根拠つきで答える' },
  catalog: { eyebrow: 'つかう', title: 'カタログ', sub: '作ったデータの中身を見渡す' },
  activity: { eyebrow: '管理', title: 'アクティビティ', sub: 'いつ・何が取り込まれたか' },
};

// Brand mark — three stars connected (the asterism). Simple circles + lines.
function BrandMark({ t, size = 26, on = 'rail' }) {
  const star = on === 'dark' ? t.accent : t.primary;
  const line = on === 'dark' ? 'rgba(255,255,255,0.4)' : t.borderStrong;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <line x1="5" y1="7" x2="18" y2="12" stroke={line} strokeWidth="1.4" />
      <line x1="5" y1="7" x2="9" y2="19" stroke={line} strokeWidth="1.4" />
      <line x1="18" y1="12" x2="9" y2="19" stroke={line} strokeWidth="1.4" />
      <circle cx="5" cy="7" r="2.4" fill={star} />
      <circle cx="18" cy="12" r="2.4" fill={star} />
      <circle cx="9" cy="19" r="2.4" fill={star} />
    </svg>
  );
}

function AppFrame({ t, active, children, topRight }) {
  const dark = t.navStyle === 'darkrail';
  const minimal = t.navStyle === 'minimal';
  const meta = TITLES[active];

  // sidebar palette
  const sbBg = dark ? t.sky : t.surface;
  const sbFg = dark ? t.skyFg : t.fg;
  const sbMuted = dark ? t.skyMuted : t.muted;
  const sbBorder = dark ? t.skyBorder : t.border;

  const grouped = [];
  let lastGroup = null;
  NAV.forEach((n) => {
    if (n.group && n.group !== lastGroup) { grouped.push({ heading: n.group }); lastGroup = n.group; }
    grouped.push(n);
  });

  const navItem = (n) => {
    const on = active === n.id;
    const I = Icons[n.icon];
    const onColor = dark ? t.accent : t.primary;
    return (
      <div key={n.id} style={{
        display: 'flex', alignItems: 'center', gap: 11, padding: minimal ? '8px 10px' : '9px 12px',
        borderRadius: t.radiusSm, cursor: 'pointer', position: 'relative',
        background: on ? (dark ? 'rgba(255,255,255,0.07)' : t.primarySoft) : 'transparent',
        color: on ? (dark ? t.skyFg : t.primary) : sbMuted,
        fontWeight: on ? 700 : 500,
      }}>
        {minimal && on && <span style={{ position: 'absolute', left: -10, top: 8, bottom: 8, width: 3, borderRadius: 3, background: t.accent }} />}
        <span style={{ color: on ? onColor : sbMuted, display: 'flex' }}><I size={18} /></span>
        <span style={{ fontSize: 14, letterSpacing: '0.01em' }}>{n.label}</span>
        <span style={{ marginLeft: 'auto', fontSize: 10.5, color: dark ? t.skyMuted : t.faint, fontFamily: t.fontMono, opacity: on ? 0.9 : 0.6 }}>{n.en}</span>
      </div>
    );
  };

  return (
    <div style={{ display: 'flex', height: '100%', width: '100%', background: t.bg, color: t.fg,
      fontFamily: t.fontUI, fontSize: 14, lineHeight: 1.6, overflow: 'hidden' }}>
      {/* Sidebar */}
      <aside style={{ width: 248, flex: '0 0 248px', background: sbBg, borderRight: `1px solid ${sbBorder}`,
        display: 'flex', flexDirection: 'column', padding: '20px 14px 16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 11, padding: '2px 6px 18px' }}>
          <BrandMark t={t} on={dark ? 'dark' : 'rail'} />
          <div style={{ lineHeight: 1.15 }}>
            <div style={{ fontFamily: t.fontDisplay, fontSize: 19, fontWeight: t.display.weight, letterSpacing: t.display.spacing, color: sbFg }}>asterism</div>
            <div style={{ fontSize: 10.5, color: sbMuted, fontFamily: t.fontMono, marginTop: 1 }}>研究データ → つながったデータ</div>
          </div>
        </div>

        <nav style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {grouped.map((n, i) => n.heading ? (
            <div key={'h' + i} style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase',
              color: dark ? t.skyMuted : t.faint, padding: '14px 8px 6px' }}>{n.heading}</div>
          ) : navItem(n))}
        </nav>

        <div style={{ marginTop: 'auto', display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '9px 12px', borderRadius: t.radiusSm,
            color: sbMuted, fontSize: 13, cursor: 'pointer', border: `1px solid ${sbBorder}` }}>
            <span style={{ display: 'flex', color: sbMuted }}><Icons.code size={16} /></span>
            SPARQL <span style={{ fontSize: 10, fontFamily: t.fontMono, marginLeft: 'auto', color: dark ? t.skyMuted : t.faint }}>開発者向け</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 6px', fontSize: 11.5, color: sbMuted }}>
            <span style={{ width: 7, height: 7, borderRadius: 4, background: t.entity, flex: '0 0 auto' }} />
            グラフ稼働中 · <span style={{ fontFamily: t.fontMono }}>1.2M</span> 件
          </div>
        </div>
      </aside>

      {/* Main */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <header style={{ display: 'flex', alignItems: 'center', gap: 16, padding: '18px 30px',
          borderBottom: `1px solid ${t.border}`, background: t.surface }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.09em', textTransform: 'uppercase', color: t.accent }}>{meta.eyebrow}</div>
            <h1 style={{ margin: '2px 0 0', fontFamily: t.fontDisplay, fontSize: 23, fontWeight: t.display.weight,
              letterSpacing: t.display.spacing, color: t.fg }}>{meta.title}</h1>
          </div>
          <span style={{ fontSize: 13, color: t.muted, alignSelf: 'flex-end', paddingBottom: 3 }}>{meta.sub}</span>
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>{topRight}</div>
        </header>
        <main style={{ flex: 1, overflow: 'hidden', padding: '24px 30px', position: 'relative' }}>
          {children}
        </main>
      </div>
    </div>
  );
}

// Small shared UI atoms (theme-driven) reused across screens.
function Btn({ t, children, kind = 'primary', icon, size = 'md' }) {
  const pad = size === 'sm' ? '7px 12px' : '10px 16px';
  const base = { display: 'inline-flex', alignItems: 'center', gap: 7, fontFamily: t.fontUI,
    fontSize: size === 'sm' ? 12.5 : 13.5, fontWeight: 600, borderRadius: t.radiusSm, padding: pad, cursor: 'pointer', whiteSpace: 'nowrap' };
  const kinds = {
    primary: { background: t.primary, color: t.primaryFg, border: `1px solid ${t.primary}` },
    accent: { background: t.accent, color: '#fff', border: `1px solid ${t.accent}` },
    ghost: { background: t.surface, color: t.fg, border: `1px solid ${t.borderStrong}` },
    soft: { background: t.primarySoft, color: t.primary, border: `1px solid transparent` },
  };
  const I = icon ? Icons[icon] : null;
  return <span style={{ ...base, ...kinds[kind] }}>{I && <I size={size === 'sm' ? 15 : 16} />}{children}</span>;
}

// "jargon" inline tag: plain word + a dotted-underline English term for the
// curious. Encodes the "hide jargon, keep it discoverable" decision.
function Term({ t, children, en }) {
  return (
    <span style={{ borderBottom: `1.5px dotted ${t.faint}`, cursor: 'help', position: 'relative' }} title={en}>
      {children}<sup style={{ fontSize: 9, color: t.faint, fontFamily: t.fontMono, marginLeft: 1 }}>?</sup>
    </span>
  );
}

function Card({ t, children, pad = 20, style }) {
  return <div style={{ background: t.surface, border: `1px solid ${t.border}`, borderRadius: t.radius, boxShadow: t.shadowSoft, padding: pad, ...style }}>{children}</div>;
}

Object.assign(window, { AppFrame, Btn, Term, Card, BrandMark, NAV });
