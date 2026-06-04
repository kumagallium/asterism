// screen-home.jsx — NEW orientation screen (currently missing). Answers "what
// do I have, and what can I do next" in plain language before any jargon.

function ScreenHome(t) {
  const dark = t.variant === 'constellation';
  const stat = (n, l, c) => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span style={{ fontFamily: t.fontMono, fontSize: 26, fontWeight: 700, color: c || t.fg, letterSpacing: '-0.02em' }}>{n}</span>
      <span style={{ fontSize: 12, color: t.muted }}>{l}</span>
    </div>
  );

  const bigAction = (icon, title, sub, primary) => {
    const I = Icons[icon];
    return (
      <div style={{
        flex: 1, borderRadius: t.radius, padding: '22px 22px', cursor: 'pointer',
        background: primary ? t.primary : t.surface,
        border: `1px solid ${primary ? t.primary : t.border}`,
        color: primary ? t.primaryFg : t.fg, boxShadow: primary ? t.shadow : t.shadowSoft,
        display: 'flex', flexDirection: 'column', gap: 12, minHeight: 132, position: 'relative', overflow: 'hidden',
      }}>
        {primary && dark && <div style={{ position: 'absolute', right: 12, top: 10, opacity: 0.5 }}><Constellation w={150} h={70} color={t.accent} dim="rgba(255,255,255,0.35)" /></div>}
        <span style={{ width: 40, height: 40, borderRadius: t.radiusSm, display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: primary ? 'rgba(255,255,255,0.16)' : t.primarySoft, color: primary ? '#fff' : t.primary }}>
          <I size={21} />
        </span>
        <div style={{ marginTop: 'auto' }}>
          <div style={{ fontFamily: t.fontDisplay, fontSize: 18, fontWeight: t.display.weight, letterSpacing: t.display.spacing }}>{title}</div>
          <div style={{ fontSize: 12.5, color: primary ? 'rgba(255,255,255,0.82)' : t.muted, marginTop: 3 }}>{sub}</div>
        </div>
        <span style={{ position: 'absolute', right: 18, bottom: 18, color: primary ? 'rgba(255,255,255,0.9)' : t.accent }}><Icons.arrow size={18} /></span>
      </div>
    );
  };

  const dsRow = (name, kind, counts, status, statusColor) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '13px 4px', borderBottom: `1px solid ${t.border}` }}>
      <span style={{ width: 34, height: 34, borderRadius: t.radiusSm, background: t.surfaceAlt, border: `1px solid ${t.border}`,
        display: 'flex', alignItems: 'center', justifyContent: 'center', color: t.muted, flex: '0 0 auto' }}><Icons.layers size={16} /></span>
      <div style={{ minWidth: 0, flex: '0 0 200px' }}>
        <div style={{ fontWeight: 600, fontSize: 13.5, color: t.fg }}>{name}</div>
        <div style={{ fontSize: 11.5, color: t.faint, fontFamily: t.fontMono }}>{kind}</div>
      </div>
      <div style={{ display: 'flex', gap: 16, flex: 1 }}>
        {counts.map((c, i) => (
          <span key={i} style={{ fontSize: 12, color: t.muted }}>
            <span style={{ fontFamily: t.fontMono, fontWeight: 700, color: t.fg }}>{c[0]}</span> {c[1]}
          </span>
        ))}
      </div>
      <span style={{ fontSize: 11.5, fontWeight: 700, padding: '4px 10px', borderRadius: 20,
        background: statusColor.bg, color: statusColor.fg, border: `1px solid ${statusColor.bd}` }}>{status}</span>
      <span style={{ color: t.faint }}><Icons.chevron size={16} /></span>
    </div>
  );

  const pub = { bg: t.entitySoft, fg: t.entity, bd: 'transparent' };
  const draft = { bg: t.accentSoft, fg: t.accent, bd: 'transparent' };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, height: '100%' }}>
      {/* status band */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 36, padding: '18px 22px', borderRadius: t.radius,
        background: dark ? t.sky : t.surface, border: `1px solid ${dark ? t.skyBorder : t.border}`,
        color: dark ? t.skyFg : t.fg, boxShadow: t.shadowSoft, position: 'relative', overflow: 'hidden' }}>
        {dark && <div style={{ position: 'absolute', right: 24, top: '50%', transform: 'translateY(-50%)', opacity: 0.8 }}><Constellation w={220} h={80} color={t.accent} /></div>}
        <div>
          <div style={{ fontSize: 12.5, color: dark ? t.skyMuted : t.muted, marginBottom: 8 }}>今ある「つながったデータ」</div>
          <div style={{ display: 'flex', gap: 34 }}>
            {stat('1.2M', '事実の数 / triples', dark ? t.skyFg : t.fg)}
            {stat('3', 'データセット', dark ? t.skyFg : t.fg)}
            {stat('5', '語彙のクラス', dark ? t.accent : t.primary)}
            {stat('100%', '出どころを追える', t.entity)}
          </div>
        </div>
      </div>

      {/* two big actions */}
      <div style={{ display: 'flex', gap: 16 }}>
        {bigAction('add', 'データを追加', 'CSV・API などをつなぐと、AI が設計を下書きします', true)}
        {bigAction('ask', '質問する', '取り込んだデータに、根拠つきで答えます', false)}
      </div>

      {/* recent datasets */}
      <Card t={t} pad={18} style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 6 }}>
          <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700, color: t.fg }}>最近のデータセット</h3>
          <span style={{ marginLeft: 'auto', fontSize: 12.5, color: t.accent, fontWeight: 600, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            カタログで全部見る <Icons.arrow size={14} />
          </span>
        </div>
        {dsRow('Starrydata 熱電データ', 'starrydata · CSV 3種', [['1.2M', '事実'], ['45k', '試料'], ['12k', '論文']], '公開済み', pub)}
        {dsRow('NIMS Supercon', '超伝導体 · API 連携', [['8.2k', '事実'], ['320', '試料'], ['88', '論文']], '下書き', draft)}
        {dsRow('実験ノート 2026Q1', 'measurement · JSON', [['—', '事実'], ['54', '試料'], ['—', '論文']], '設計中', { bg: t.surfaceAlt, fg: t.muted, bd: t.border })}
      </Card>
    </div>
  );
}

Object.assign(window, { ScreenHome });
