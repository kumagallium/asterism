// screen-catalog.jsx — Catalog (redesigned Gallery). Datasets are the entry
// point. The OLD abstract split (Ontologies vs Mappings as sibling galleries)
// is NOT deleted — it is RE-HOMED: every dataset HAS a 設計図 (=ontology/語彙)
// and 取り込みルール (=mapping). They live as two tabs inside the dataset, so the
// concepts stay but attach to something concrete the user owns. The ontology
// that is SHARED across datasets gets promoted to its own board (ScreenSharedVocab).

function ScreenCatalog(t) {
  return <CatalogView t={t} />;
}

function CatalogView({ t }) {
  const { useState } = React;
  const [tab, setTab] = useState('design'); // 'design' | 'rules'

  const statusChip = (txt, kind) => {
    const c = kind === 'pub' ? { bg: t.entitySoft, fg: t.entity } : kind === 'draft' ? { bg: t.accentSoft, fg: t.accent } : { bg: t.surfaceAlt, fg: t.muted };
    return <span style={{ fontSize: 10.5, fontWeight: 700, padding: '3px 9px', borderRadius: 20, background: c.bg, color: c.fg }}>{txt}</span>;
  };
  const purposeTag = (txt) => (
    <span key={txt} style={{ fontSize: 11, fontWeight: 600, padding: '3px 9px', borderRadius: 20, background: t.primarySoft, color: t.primary }}>{txt}</span>
  );
  const dsCard = (name, sub, status, statusKind, counts, active) => (
    <div style={{ borderRadius: t.radiusSm, border: `1.5px solid ${active ? t.primary : t.border}`, background: t.surface,
      boxShadow: active ? t.shadow : t.shadowSoft, padding: '13px 14px', cursor: 'pointer', position: 'relative' }}>
      {active && <span style={{ position: 'absolute', left: -1.5, top: 12, bottom: 12, width: 3, borderRadius: 3, background: t.primary }} />}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontWeight: 700, fontSize: 13.5, color: t.fg }}>{name}</span>
        <span style={{ marginLeft: 'auto' }}>{statusChip(status, statusKind)}</span>
      </div>
      <div style={{ fontSize: 11, color: t.faint, fontFamily: t.fontMono, marginTop: 2 }}>{sub}</div>
      <div style={{ display: 'flex', gap: 12, marginTop: 9 }}>
        {counts.map((c, i) => (
          <span key={i} style={{ fontSize: 11, color: t.muted }}>
            <span style={{ fontFamily: t.fontMono, fontWeight: 700, color: t.fg }}>{c[0]}</span> {c[1]}
          </span>
        ))}
      </div>
    </div>
  );
  const reuseChip = (prefix, what) => (
    <span key={prefix} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, padding: '4px 9px', borderRadius: 20, background: t.surfaceAlt, border: `1px solid ${t.border}` }}>
      <code style={{ fontFamily: t.fontMono, color: t.activity, fontWeight: 700 }}>{prefix}</code>
      <span style={{ color: t.muted }}>{what}</span>
    </span>
  );
  const ruleRow = (item, target, conv) => (
    <tr>
      <td style={{ padding: '8px 10px', fontFamily: t.fontMono, fontSize: 11.5, color: t.fg, borderBottom: `1px solid ${t.border}` }}>{item}</td>
      <td style={{ padding: '8px 4px', color: t.faint, borderBottom: `1px solid ${t.border}`, textAlign: 'center' }}><Icons.arrow size={13} /></td>
      <td style={{ padding: '8px 10px', borderBottom: `1px solid ${t.border}` }}><span style={{ fontWeight: 600, fontSize: 12, color: t.entity }}>{target}</span></td>
      <td style={{ padding: '8px 10px', borderBottom: `1px solid ${t.border}` }}><span style={{ fontSize: 10.5, color: t.activity, fontFamily: t.fontMono, background: t.activitySoft, padding: '2px 7px', borderRadius: 20 }}>{conv}</span></td>
    </tr>
  );
  const tabBtn = (id, lbl, en) => {
    const on = tab === id;
    return (
      <span onClick={() => setTab(id)} style={{ display: 'inline-flex', alignItems: 'baseline', gap: 6, fontSize: 12.5, fontWeight: 600,
        padding: '5px 13px', borderRadius: 20, cursor: 'pointer', background: on ? t.primarySoft : 'transparent', color: on ? t.primary : t.muted }}>
        {lbl}<span style={{ fontSize: 9.5, fontFamily: t.fontMono, color: on ? t.primary : t.faint, opacity: 0.8 }}>{en}</span>
      </span>
    );
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14, height: '100%' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 12.5, color: t.muted }}>
        <Icons.layers size={16} />
        作った<strong style={{ color: t.fg }}>データセット</strong>が主役です。各データセットは「<strong style={{ color: t.fg }}>設計図（語彙）</strong>」と「<strong style={{ color: t.fg }}>取り込みルール</strong>」を持ちます。
        共通で使う<Term t={t} en="shared ontology / TBox">語彙</Term>は下にまとめています。
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '300px 1fr', gap: 16, flex: 1, minHeight: 0 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, minHeight: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <h3 style={{ margin: 0, fontSize: 13, fontWeight: 700, color: t.fg }}>データセット</h3>
            <span style={{ fontSize: 11, fontFamily: t.fontMono, color: t.faint }}>3</span>
            <span style={{ marginLeft: 'auto' }}><Btn t={t} kind="soft" size="sm" icon="add">追加</Btn></span>
          </div>
          {dsCard('Starrydata 熱電データ', 'starrydata · CSV 3種', '公開済み', 'pub', [['1.2M', '事実'], ['45k', '試料'], ['12k', '論文']], true)}
          {dsCard('NIMS Supercon', 'API 連携 · superconductors', '下書き', 'draft', [['8.2k', '事実'], ['320', '試料']], false)}
          {dsCard('実験ノート 2026Q1', 'JSON · measurement', '設計中', 'design', [['—', '未取込']], false)}
        </div>

        <Card t={t} pad={0} style={{ display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
          <div style={{ padding: '15px 20px', borderBottom: `1px solid ${t.border}` }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <h2 style={{ margin: 0, fontFamily: t.fontDisplay, fontSize: 19, fontWeight: t.display.weight, letterSpacing: t.display.spacing, color: t.fg }}>Starrydata 熱電データ</h2>
              {statusChip('公開済み', 'pub')}
              <span style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
                {tabBtn('design', '設計図', 'ontology')}
                {tabBtn('rules', '取り込みルール', 'mapping')}
              </span>
            </div>
            <div style={{ marginTop: 12 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: t.muted, marginBottom: 7, display: 'flex', alignItems: 'center', gap: 6 }}>
                <Icons.ask size={13} /> このデータが答えられる問い
              </div>
              <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>
                {['熱電性能の探索', '組成検索', '単位の正規化 (QUDT)', '来歴トレース', '論文メタ参照'].map(purposeTag)}
              </div>
            </div>
          </div>

          {tab === 'design' ? (
            <div style={{ padding: '14px 20px', flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                <span style={{ fontSize: 12.5, fontWeight: 700, color: t.fg }}>設計図（中身の構造）</span>
                <span style={{ fontSize: 11, color: t.faint }}>5 クラス · すべて出どころ付き</span>
                <span style={{ marginLeft: 'auto', fontSize: 11, color: t.muted, display: 'inline-flex', alignItems: 'center', gap: 5, cursor: 'pointer' }}><Icons.doc size={13} /> TTL</span>
              </div>
              <div style={{ background: t.surfaceAlt, borderRadius: t.radiusSm, border: `1px solid ${t.border}`, padding: '8px', flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 0 }}>
                <ClassDiagram t={t} compact />
              </div>
              <div style={{ marginTop: 12, fontSize: 11.5, fontWeight: 700, color: t.muted, marginBottom: 7 }}>他から借りている語彙（再発明しない）</div>
              <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>
                {reuseChip('qudt:', '物性名・単位')}
                {reuseChip('schema:', '論文メタ')}
                {reuseChip('prov:', '来歴')}
                {reuseChip('dcterms:', 'ID')}
              </div>
            </div>
          ) : (
            <div style={{ padding: '14px 20px', flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <span style={{ fontSize: 12.5, fontWeight: 700, color: t.fg }}>取り込みルール（項目の対応）</span>
                <span style={{ marginLeft: 'auto', fontSize: 11, color: t.muted, display: 'inline-flex', alignItems: 'center', gap: 5, cursor: 'pointer' }}><Icons.code size={13} /> ingester</span>
              </div>
              <p style={{ margin: '0 0 8px', fontSize: 11.5, color: t.muted }}>ソースの各項目を、設計図のどこへどう変換してつなぐか。</p>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ fontSize: 10, color: t.faint, textAlign: 'left', fontFamily: t.fontMono }}>
                    <th style={{ padding: '0 10px 6px', fontWeight: 600 }}>ソース項目</th><th></th>
                    <th style={{ padding: '0 10px 6px', fontWeight: 600 }}>つなぐ先</th>
                    <th style={{ padding: '0 10px 6px', fontWeight: 600 }}>変換</th>
                  </tr>
                </thead>
                <tbody>
                  {ruleRow('SID + sample_id', '試料のID', '複合キー')}
                  {ruleRow('composition', '組成', 'そのまま')}
                  {ruleRow('Seebeck_coef', 'ゼーベック係数', 'QUDT単位')}
                  {ruleRow('Seebeck_unit', '単位', '表記ゆれ正規化')}
                  {ruleRow('temperature', '測定温度', '°C→K')}
                  {ruleRow('doi', '論文', 'schema:同定')}
                </tbody>
              </table>
              <div style={{ marginTop: 12, display: 'flex', gap: 9, flexWrap: 'wrap' }}>
                {[['MIE', 'mapping.json', '機械可読の対応表'], ['CODE', 'ingester.py', '実際の取り込み処理']].map(([k, f, d]) => (
                  <div key={f} style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '8px 11px', borderRadius: t.radiusSm, background: t.surfaceAlt, border: `1px solid ${t.border}` }}>
                    <span style={{ fontSize: 9, fontWeight: 700, fontFamily: t.fontMono, color: '#fff', background: t.activity, padding: '2px 6px', borderRadius: 4 }}>{k}</span>
                    <code style={{ fontSize: 11.5, fontFamily: t.fontMono, color: t.fg }}>{f}</code>
                    <span style={{ fontSize: 10.5, color: t.muted }}>{d}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </Card>
      </div>

      {/* shared vocabulary band — gateway to the shared-vocab board */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, padding: '14px 20px', borderRadius: t.radius,
        background: t.surface, border: `1px solid ${t.border}`, borderLeft: `3px solid ${t.activity}`, boxShadow: t.shadowSoft, cursor: 'pointer' }}>
        <span style={{ width: 38, height: 38, borderRadius: t.radiusSm, background: t.activitySoft, color: t.activity, display: 'flex', alignItems: 'center', justifyContent: 'center', flex: '0 0 auto' }}><Icons.link size={19} /></span>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <span style={{ fontWeight: 700, fontSize: 13.5, color: t.fg }}>共有の語彙</span>
            <span style={{ fontSize: 10.5, fontFamily: t.fontMono, color: t.faint }}>shared vocabulary</span>
            <span style={{ fontSize: 10.5, fontWeight: 700, color: t.accent, background: t.accentSoft, padding: '2px 8px', borderRadius: 20 }}>変更は全体に影響 · 要注意</span>
          </div>
          <div style={{ fontSize: 12, color: t.muted, marginTop: 3 }}>
            複数のデータセットが共通で使う設計図。揃えておくと<strong style={{ color: t.fg }}>横断して検索・比較</strong>できます。
          </div>
        </div>
        <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 16, flex: '0 0 auto' }}>
          <span style={{ fontSize: 12, color: t.muted }}><span style={{ fontFamily: t.fontMono, fontWeight: 700, color: t.fg }}>2</span> データセットが利用</span>
          <span style={{ fontSize: 12.5, color: t.activity, fontWeight: 600, display: 'inline-flex', alignItems: 'center', gap: 4 }}>開く <Icons.arrow size={14} /></span>
        </span>
      </div>
    </div>
  );
}

Object.assign(window, { ScreenCatalog, CatalogView });
