// screen-vocab.jsx — The shared-vocabulary board. This is the ANSWER to the
// user's question: "datasets に絞る＝ontology/mapping は無くなる？" → No. The
// vocabulary (ontology) is still first-class; it is just SHARED across datasets
// instead of floating as an abstract gallery. This board shows: (1) the shared
// classes, (2) WHICH datasets use it and HOW they bind in (4 strategies:
// そのまま使う / 広げる / つなぐ / 新規), (3) impact = why edits ripple downstream.

function ScreenSharedVocab(t) {
  // binding strategy → color + plain meaning
  const BIND = {
    reuse:  { label: 'そのまま使う', en: 'reuse',  c: t.entity,   bg: t.entitySoft },
    extend: { label: '広げる',       en: 'extend', c: t.activity,  bg: t.activitySoft },
    map:    { label: 'つなぐ',       en: 'map-into', c: t.accent,  bg: t.accentSoft },
    new:    { label: '新規',         en: 'new',    c: t.muted,     bg: t.surfaceAlt },
  };
  const bindChip = (k) => {
    const b = BIND[k];
    return <span style={{ display: 'inline-flex', alignItems: 'baseline', gap: 5, fontSize: 11, fontWeight: 700, padding: '3px 9px', borderRadius: 20, background: b.bg, color: b.c }}>
      {b.label}<span style={{ fontSize: 9, fontFamily: t.fontMono, opacity: 0.75 }}>{b.en}</span>
    </span>;
  };

  // the shared classes
  const classes = [
    ['試料', 'Sample', '組成・相をもつ物質サンプル'],
    ['測定曲線', 'Curve', '温度 vs 物性の一連の測定'],
    ['論文', 'Paper', '出典の文献メタ'],
    ['記述子', 'Descriptor', '物性名（QUDTに整合）'],
    ['取り込み記録', 'IngestionActivity', 'いつ・何から作られたか'],
  ];

  // datasets that bind into the shared vocab
  const users = [
    { name: 'Starrydata 熱電データ', src: 'CSV · 1.2M 事実', binds: [['試料', 'reuse'], ['測定曲線', 'reuse'], ['論文', 'reuse'], ['記述子', 'map']] },
    { name: 'NIMS Supercon', src: 'API · 8.2k 事実', binds: [['試料', 'reuse'], ['記述子', 'extend'], ['臨界温度Tc', 'new']] },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14, height: '100%' }}>
      {/* answer banner */}
      <div style={{ display: 'flex', gap: 14, padding: '14px 18px', borderRadius: t.radius, background: t.surfaceAlt, border: `1px solid ${t.border}`, borderLeft: `3px solid ${t.activity}` }}>
        <span style={{ width: 36, height: 36, borderRadius: t.radiusSm, background: t.activitySoft, color: t.activity, display: 'flex', alignItems: 'center', justifyContent: 'center', flex: '0 0 auto' }}><Icons.link size={19} /></span>
        <div>
          <div style={{ fontSize: 13.5, fontWeight: 700, color: t.fg, marginBottom: 3 }}>「設計図（語彙）」は無くなりません — <span style={{ color: t.activity }}>共有</span>されるだけ</div>
          <div style={{ fontSize: 12.5, color: t.muted, lineHeight: 1.7 }}>
            データセットを主役にしても、<Term t={t} en="ontology">語彙</Term>と<Term t={t} en="mapping">取り込みルール</Term>は各データセットの中に残ります。
            ここはそのうち<strong style={{ color: t.fg }}>みんなで共通して使う部分</strong>。揃えるほど横断検索・比較が効きます。
          </div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.2fr', gap: 16, flex: 1, minHeight: 0 }}>
        {/* shared classes */}
        <Card t={t} pad={0} style={{ display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
          <div style={{ padding: '14px 18px', borderBottom: `1px solid ${t.border}`, display: 'flex', alignItems: 'center', gap: 9 }}>
            <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>共有クラス</h3>
            <span style={{ fontSize: 11, fontFamily: t.fontMono, color: t.faint }}>5 · materials-core v1.2</span>
            <span style={{ marginLeft: 'auto' }}>{/* spacer */}</span>
          </div>
          <div style={{ padding: '10px 14px', overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 7 }}>
            {classes.map(([ja, en, desc]) => (
              <div key={en} style={{ display: 'flex', alignItems: 'center', gap: 11, padding: '9px 12px', borderRadius: t.radiusSm, border: `1px solid ${t.border}`, background: t.surface, borderTop: `3px solid ${t.entity}` }}>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 7 }}>
                    <span style={{ fontWeight: 700, fontSize: 13, color: t.fg }}>{ja}</span>
                    <code style={{ fontSize: 10.5, fontFamily: t.fontMono, color: t.faint }}>{en}</code>
                  </div>
                  <div style={{ fontSize: 11.5, color: t.muted, marginTop: 1 }}>{desc}</div>
                </div>
                <span style={{ fontSize: 11, color: t.muted }}><span style={{ fontFamily: t.fontMono, fontWeight: 700, color: t.fg }}>2</span> 利用</span>
              </div>
            ))}
          </div>
        </Card>

        {/* who uses it + how (binding strategy) */}
        <Card t={t} pad={0} style={{ display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
          <div style={{ padding: '14px 18px', borderBottom: `1px solid ${t.border}`, display: 'flex', alignItems: 'center', gap: 9 }}>
            <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>どのデータが、どう使っているか</h3>
            <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
              {Object.keys(BIND).map((k) => <React.Fragment key={k}>{bindChip(k)}</React.Fragment>)}
            </span>
          </div>
          <div style={{ padding: '12px 16px', overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 12 }}>
            {users.map((u) => (
              <div key={u.name} style={{ borderRadius: t.radiusSm, border: `1px solid ${t.border}`, background: t.surface, padding: '12px 14px', boxShadow: t.shadowSoft }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
                  <span style={{ width: 28, height: 28, borderRadius: t.radiusSm, background: t.primarySoft, color: t.primary, display: 'flex', alignItems: 'center', justifyContent: 'center', flex: '0 0 auto' }}><Icons.layers size={15} /></span>
                  <span style={{ fontWeight: 700, fontSize: 13, color: t.fg }}>{u.name}</span>
                  <span style={{ fontSize: 10.5, fontFamily: t.fontMono, color: t.faint }}>{u.src}</span>
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 10 }}>
                  {u.binds.map(([cls, strat]) => (
                    <span key={cls} style={{ display: 'inline-flex', alignItems: 'center', gap: 7, padding: '4px 6px 4px 10px', borderRadius: 20, background: t.surfaceAlt, border: `1px solid ${t.border}` }}>
                      <span style={{ fontSize: 12, fontWeight: 600, color: t.fg }}>{cls}</span>
                      {bindChip(strat)}
                    </span>
                  ))}
                </div>
              </div>
            ))}
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 9, padding: '11px 13px', borderRadius: t.radiusSm, background: t.accentSoft, border: `1px solid ${t.accent}22` }}>
              <span style={{ color: t.accent, flex: '0 0 auto', marginTop: 1 }}><Icons.activity size={16} /></span>
              <div style={{ fontSize: 11.5, color: t.fg, lineHeight: 1.6 }}>
                <strong>なぜ「要注意」？</strong> 共有クラスを書き換えると、それを使う <span style={{ fontFamily: t.fontMono, fontWeight: 700 }}>2</span> データセット
                すべての検索・回答に波及します。変更は<strong>影響範囲のプレビュー</strong>を見てから確定します。
              </div>
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}

Object.assign(window, { ScreenSharedVocab });
