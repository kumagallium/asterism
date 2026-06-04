// screen-ask.jsx — grounded answer + clickable citations + data-quality notes,
// with the provenance trace open as a right panel (the demo "wow"). The
// constellation direction renders the trace as a literal star-chain on a night
// sky; forest/clean use a light rail with PROV-DM colored nodes.

function ScreenAsk(t) {
  const dark = t.variant === 'constellation';

  const examples = ['ZT が最も高い熱電材料は？', 'SnSe を含む試料は？', '新しく設計したデータには何がある？'];

  // citation card
  const cite = (kind, label, fields, color, soft) => (
    <div style={{ border: `1px solid ${t.border}`, borderRadius: t.radiusSm, background: t.surface, overflow: 'hidden',
      display: 'flex', boxShadow: t.shadowSoft, cursor: 'pointer' }}>
      <span style={{ width: 5, background: color, flex: '0 0 auto' }} />
      <div style={{ padding: '10px 13px', flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em', color: '#fff',
            background: color, padding: '2px 7px', borderRadius: 4, fontFamily: t.fontMono }}>{kind}</span>
          <span style={{ fontWeight: 700, fontSize: 13.5, color: t.fg }}>{label}</span>
          <span style={{ marginLeft: 'auto', color: t.faint, display: 'inline-flex', alignItems: 'center', gap: 3, fontSize: 11 }}>
            <Icons.trace size={13} /> 出どころ
          </span>
        </div>
        <div style={{ display: 'flex', gap: 12, marginTop: 6 }}>
          {fields.map(([k, v]) => (
            <span key={k} style={{ fontSize: 11.5, color: t.muted }}>
              <span style={{ fontFamily: t.fontMono, fontSize: 10.5, color: t.faint }}>{k}</span>{' '}
              <span style={{ color: t.fg, fontWeight: 600 }}>{v}</span>
            </span>
          ))}
        </div>
      </div>
    </div>
  );

  // provenance chain node
  const STEP = [
    { ja: '測定曲線', en: 'curve', label: 'Fig.3 ZT vs T', detail: 'yMax = 2.6', entity: true },
    { ja: '試料', en: 'sample', label: 'SnSe', detail: 'composition = SnSe', entity: true },
    { ja: '論文', en: 'paper', label: 'Snyder et al. (2014)', detail: 'DOI 10.1038/nature13184', entity: true },
    { ja: 'デジタル化', en: 'digitization', label: 'WebPlotDigitizer', detail: 'Fig.3 から読み取り', entity: false },
    { ja: '取り込み', en: 'ingestion', label: '取り込み記録', detail: '2026-05-31', entity: false },
  ];

  const node = (s, i, last) => {
    const color = s.entity ? t.entity : t.activity;
    const starColor = dark ? (s.entity ? '#7fd3a0' : '#8fb3ef') : color;
    return (
      <div key={i} style={{ display: 'flex', gap: 13 }}>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flex: '0 0 auto', width: 16 }}>
          {dark ? (
            <svg width="16" height="16" viewBox="0 0 16 16"><circle cx="8" cy="8" r="4" fill={starColor} /><circle cx="8" cy="8" r="7" fill="none" stroke={starColor} strokeWidth="0.8" opacity="0.4" /></svg>
          ) : (
            <span style={{ width: 13, height: 13, borderRadius: 999, background: color, marginTop: 2, boxShadow: `0 0 0 4px ${s.entity ? t.entitySoft : t.activitySoft}` }} />
          )}
          {!last && <span style={{ width: dark ? 1 : 2, flex: 1, minHeight: 26, background: dark ? t.skyBorder : t.border, marginTop: 4 }} />}
        </div>
        <div style={{ paddingBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 10.5, fontWeight: 700, color: '#fff', background: color, padding: '1px 7px', borderRadius: 4 }}>{s.ja}</span>
            <span style={{ fontWeight: 700, fontSize: 13, color: dark ? t.skyFg : t.fg }}>{s.label}</span>
          </div>
          <div style={{ fontSize: 11.5, color: dark ? t.skyMuted : t.muted, marginTop: 3 }}>{s.detail}</div>
          <div style={{ fontSize: 10, fontFamily: t.fontMono, color: dark ? 'rgba(255,255,255,0.3)' : t.faint, marginTop: 2 }}>resource/{s.en}/…</div>
        </div>
      </div>
    );
  };

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr', gap: 16, height: '100%' }}>
      {/* left: question + answer */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14, minHeight: 0 }}>
        {/* ask bar */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
          <div style={{ display: 'flex', gap: 9 }}>
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 9, padding: '11px 15px', borderRadius: t.radius,
              background: t.surface, border: `1.5px solid ${t.primary}`, boxShadow: t.shadowSoft }}>
              <span style={{ color: t.primary, display: 'flex' }}><Icons.ask size={17} /></span>
              <span style={{ fontSize: 14, color: t.fg }}>ZT が最も高い熱電材料は？</span>
              <span style={{ marginLeft: 'auto', width: 2, height: 17, background: t.primary, opacity: 0.6 }} />
            </div>
            <Btn t={t} kind="primary">質問する</Btn>
          </div>
          <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>
            {examples.map((e) => (
              <span key={e} style={{ fontSize: 11.5, padding: '4px 11px', borderRadius: 20, background: t.surfaceAlt,
                border: `1px solid ${t.border}`, color: t.muted, cursor: 'pointer' }}>{e}</span>
            ))}
          </div>
        </div>

        {/* answer */}
        <Card t={t} pad={20} style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            <span style={{ fontSize: 10.5, fontWeight: 700, color: t.entity, background: t.entitySoft, padding: '3px 9px', borderRadius: 20,
              display: 'inline-flex', alignItems: 'center', gap: 5 }}><Icons.check size={12} /> 根拠つきの回答</span>
            <span style={{ fontSize: 11, color: t.faint }}>取り込み済みのデータに基づく</span>
          </div>
          <p style={{ margin: 0, fontSize: 16.5, lineHeight: 1.85, color: t.fg, fontFamily: t.fontDisplay, fontWeight: dark ? 400 : 500, letterSpacing: t.display.spacing }}>
            記録上の最大は <strong style={{ color: t.primary }}>SnSe の約 2.6</strong>
            <sup style={{ fontSize: 10, color: t.entity, fontWeight: 700 }}>［1］</sup>。
            ただし <span style={{ fontFamily: t.fontMono, fontSize: 14 }}>ZT &gt; 3.5</span> の極端な値が数件ありましたが、
            軸ラベルの誤りの可能性として<span style={{ color: t.accent, fontWeight: 600 }}>除外</span>しています。
          </p>

          <div style={{ marginTop: 18, marginBottom: 8, fontSize: 12, fontWeight: 700, color: t.muted, display: 'flex', alignItems: 'center', gap: 7 }}>
            根拠（引用） <span style={{ fontSize: 10.5, fontFamily: t.fontMono, color: t.faint, fontWeight: 500 }}>クリックで出どころを表示</span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {cite('測定曲線', 'Fig.3 ZT vs T', [['propertyY', 'ZT'], ['yMax', '2.6']], t.entity, t.entitySoft)}
            {cite('試料', 'SnSe', [['composition', 'SnSe']], t.entity, t.entitySoft)}
          </div>

          <div style={{ marginTop: 14, display: 'flex', gap: 10, padding: '11px 13px', borderRadius: t.radiusSm,
            background: t.accentSoft, border: `1px solid ${t.accent}33` }}>
            <span style={{ color: t.accent, flex: '0 0 auto', marginTop: 1 }}><Icons.dot size={12} /></span>
            <div style={{ fontSize: 12, color: t.fg }}>
              <span style={{ fontWeight: 700 }}>データ品質メモ</span> — 物理的にあり得ない ZT（&gt;3.5）はデータ誤りの可能性として除外しました。
            </div>
          </div>
        </Card>
      </div>

      {/* right: provenance trace (open) */}
      <div style={{ borderRadius: t.radius, overflow: 'hidden', display: 'flex', flexDirection: 'column',
        background: dark ? t.sky : t.surface, border: `1px solid ${dark ? t.skyBorder : t.border}`, boxShadow: t.shadow }}>
        <div style={{ padding: '15px 18px', borderBottom: `1px solid ${dark ? t.skyBorder : t.border}`, position: 'relative', overflow: 'hidden' }}>
          {dark && <div style={{ position: 'absolute', right: 8, top: 6, opacity: 0.7 }}><Constellation w={150} h={56} color={t.accent} /></div>}
          <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: dark ? t.accent : t.accent }}>出どころ · provenance</div>
          <h3 style={{ margin: '3px 0 0', fontFamily: t.fontDisplay, fontSize: 17, fontWeight: t.display.weight, color: dark ? t.skyFg : t.fg, letterSpacing: t.display.spacing }}>来歴をたどる</h3>
          <p style={{ margin: '5px 0 0', fontSize: 11.5, color: dark ? t.skyMuted : t.muted }}>
            この数字が<strong style={{ color: dark ? t.skyFg : t.fg }}>どの曲線・論文・取り込み</strong>から来たか、星をつなぐように追えます。
          </p>
        </div>
        <div style={{ padding: '18px 18px 6px', flex: 1, overflow: 'hidden' }}>
          {STEP.map((s, i) => node(s, i, i === STEP.length - 1))}
        </div>
        <div style={{ padding: '10px 18px 16px', borderTop: `1px solid ${dark ? t.skyBorder : t.border}`, display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ width: 9, height: 9, borderRadius: 2, background: t.entity }} />
          <span style={{ fontSize: 11, color: dark ? t.skyMuted : t.muted }}>データ</span>
          <span style={{ width: 9, height: 9, borderRadius: 2, background: t.activity, marginLeft: 8 }} />
          <span style={{ fontSize: 11, color: dark ? t.skyMuted : t.muted }}>処理</span>
          <span style={{ marginLeft: 'auto', fontSize: 11.5, color: t.accent, fontWeight: 600, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            語彙を見る <Icons.arrow size={13} />
          </span>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { ScreenAsk });
