// screen-add.jsx — the Workbench, reframed as a guided 3-step flow with plain
// language. The AI proposal is rendered as a READABLE design (diagram + mapping
// rules + purpose), not raw markdown; RDF artifacts hide behind a "詳細" toggle.

function ScreenAdd(t) {
  const STEPS = [
    { n: 1, label: 'AI が設計', en: 'design', done: true },
    { n: 2, label: '確認・修正', en: 'review', active: true },
    { n: 3, label: '保存', en: 'save' },
  ];

  const step = (s, i, last) => (
    <div key={s.n} style={{ display: 'flex', alignItems: 'center', gap: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
        <span style={{ width: 26, height: 26, borderRadius: 999, display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 13, fontWeight: 700, fontFamily: t.fontMono,
          background: s.done ? t.entity : s.active ? t.primary : t.surface,
          color: (s.done || s.active) ? '#fff' : t.faint,
          border: `1px solid ${s.done ? t.entity : s.active ? t.primary : t.borderStrong}` }}>
          {s.done ? '✓' : s.n}
        </span>
        <div style={{ lineHeight: 1.1 }}>
          <div style={{ fontSize: 13.5, fontWeight: s.active ? 700 : 600, color: s.active || s.done ? t.fg : t.faint }}>{s.label}</div>
          <div style={{ fontSize: 9.5, fontFamily: t.fontMono, color: t.faint }}>{s.en}</div>
        </div>
      </div>
      {!last && <span style={{ width: 46, height: 1.5, background: t.borderStrong, margin: '0 16px' }} />}
    </div>
  );

  const ruleRow = (col, arrow, target, note) => (
    <tr>
      <td style={{ padding: '8px 10px', fontFamily: t.fontMono, fontSize: 12, color: t.fg, borderBottom: `1px solid ${t.border}` }}>{col}</td>
      <td style={{ padding: '8px 6px', color: t.faint, borderBottom: `1px solid ${t.border}`, textAlign: 'center' }}><Icons.arrow size={14} /></td>
      <td style={{ padding: '8px 10px', borderBottom: `1px solid ${t.border}` }}>
        <span style={{ fontWeight: 600, fontSize: 12.5, color: t.entity }}>{target}</span>
      </td>
      <td style={{ padding: '8px 10px', fontSize: 11.5, color: t.muted, borderBottom: `1px solid ${t.border}` }}>{note}</td>
    </tr>
  );

  const checks = [
    ['IDの重複なし', 'ok'], ['文字コード安全', 'ok'], ['空のノードなし', 'ok'], ['探索メタ 5項目+', 'ok'],
    ['図ラベル安全', 'ok'], ['実在する行から', 'ok'], ['設計理由つき', 'warn'], ['幻覚チェック', 'ok'],
  ];
  const glyph = { ok: '✓', warn: '⚠', fail: '✗' };
  const cColor = { ok: t.entity, warn: t.accent, fail: '#b4453a' };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, height: '100%' }}>
      {/* data source strip — source-agnostic (CSV / JSON / API / DB) */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: '12px 16px', borderRadius: t.radius,
        background: t.surfaceAlt, border: `1px solid ${t.border}` }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 11.5, fontWeight: 700, color: t.muted }}>データソース</span>
          <div style={{ display: 'flex', gap: 4, background: t.surface, border: `1px solid ${t.border}`, borderRadius: 999, padding: 3 }}>
            {[['表計算 / CSV', 'file', true], ['JSON', 'code', false], ['API', 'link', false], ['DB', 'layers', false]].map(([lbl, ic, on]) => {
              const I = Icons[ic];
              return (
                <span key={lbl} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 600,
                  padding: '5px 12px', borderRadius: 999, cursor: 'pointer',
                  background: on ? t.primary : 'transparent', color: on ? t.primaryFg : t.muted }}>
                  <I size={14} /> {lbl}
                </span>
              );
            })}
          </div>
          <span style={{ fontSize: 11, color: t.faint }}>あらゆる構造化ソースに対応（順次拡大）</span>
          <span style={{ marginLeft: 'auto', fontSize: 12.5, color: t.muted, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
            <Icons.search size={14} /> 構造を見る
            <span style={{ fontSize: 9.5, fontFamily: t.fontMono, color: t.faint }}>(AI不要)</span>
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, paddingTop: 2, borderTop: `1px solid ${t.border}` }}>
          <span style={{ display: 'flex', color: t.primary, marginTop: 8 }}><Icons.file size={17} /></span>
          <div style={{ display: 'flex', gap: 7, marginTop: 8 }}>
            {['papers.csv', 'samples.csv', 'curves.csv'].map((f) => (
              <span key={f} style={{ fontFamily: t.fontMono, fontSize: 11.5, padding: '3px 9px', borderRadius: 20,
                background: t.surface, border: `1px solid ${t.border}`, color: t.fg }}>{f}</span>
            ))}
          </div>
          <span style={{ fontSize: 12, color: t.muted, marginTop: 8 }}>3 ファイル · 全ステップで共有</span>
          <span style={{ marginLeft: 'auto', marginTop: 8 }}><Btn t={t} kind="ghost" size="sm" icon="upload">ソースを変更</Btn></span>
        </div>
      </div>

      {/* stepper */}
      <div style={{ display: 'flex', alignItems: 'center', padding: '4px 2px' }}>
        {STEPS.map((s, i) => step(s, i, i === STEPS.length - 1))}
        <span style={{ marginLeft: 'auto', fontSize: 12, color: t.muted }}>
          かかった時間 <span style={{ fontFamily: t.fontMono, color: t.fg }}>4分12秒</span> · 設計の下書きができました
        </span>
      </div>

      {/* main 2-col */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.55fr 1fr', gap: 16, flex: 1, minHeight: 0 }}>
        {/* proposed design */}
        <Card t={t} pad={0} style={{ display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
          <div style={{ padding: '14px 18px', borderBottom: `1px solid ${t.border}`, display: 'flex', alignItems: 'center', gap: 9 }}>
            <span style={{ display: 'flex', color: t.accent }}><Icons.spark size={17} /></span>
            <h3 style={{ margin: 0, fontSize: 14.5, fontWeight: 700 }}>AI が提案した設計</h3>
            <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
              {['設計図', '取り込みルール'].map((tab, i) => (
                <span key={tab} style={{ fontSize: 12, fontWeight: 600, padding: '4px 11px', borderRadius: 20,
                  background: i === 0 ? t.primarySoft : 'transparent', color: i === 0 ? t.primary : t.muted, cursor: 'pointer' }}>{tab}</span>
              ))}
            </span>
          </div>
          <div style={{ padding: '12px 18px 16px', overflow: 'hidden', flex: 1 }}>
            <p style={{ margin: '2px 0 8px', fontSize: 12.5, color: t.muted }}>
              このデータを「<span style={{ fontWeight: 600, color: t.fg }}>論文・試料・測定曲線</span>」の3つに整理し、出どころを全部つなぎました。
            </p>
            <div style={{ background: t.surfaceAlt, borderRadius: t.radiusSm, border: `1px solid ${t.border}`, padding: '10px 8px' }}>
              <ClassDiagram t={t} compact />
            </div>
            <details style={{ marginTop: 12 }}>
              <summary style={{ fontSize: 12, color: t.muted, cursor: 'pointer', listStyle: 'none', display: 'flex', alignItems: 'center', gap: 6 }}>
                <Icons.chevron size={13} /> 詳しい設計図（<Term t={t} en="TBox / ontology / RML / MIE">語彙・取り込みルールのコード</Term>）を見る
              </summary>
            </details>
            <div style={{ marginTop: 12, fontSize: 12.5, fontWeight: 700, color: t.fg, marginBottom: 6 }}>項目の対応（取り込みルールの一部）</div>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ fontSize: 10.5, color: t.faint, textAlign: 'left', fontFamily: t.fontMono }}>
                  <th style={{ padding: '0 10px 6px', fontWeight: 600 }}>ソースの項目</th><th></th>
                  <th style={{ padding: '0 10px 6px', fontWeight: 600 }}>つなぐ先</th>
                  <th style={{ padding: '0 10px 6px', fontWeight: 600 }}>メモ</th>
                </tr>
              </thead>
              <tbody>
                {ruleRow('SID + sample_id', '→', '試料のID', '複合キーで一意に')}
                {ruleRow('composition', '→', '組成', '文字列でそのまま')}
                {ruleRow('Seebeck_unit', '→', '単位 (QUDT)', '表記ゆれを正規化')}
              </tbody>
            </table>
          </div>
        </Card>

        {/* review + quality + save */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, minHeight: 0 }}>
          <Card t={t} pad={16}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
              <h3 style={{ margin: 0, fontSize: 13.5, fontWeight: 700 }}>品質チェック</h3>
              <span style={{ fontSize: 11, color: t.faint, fontFamily: t.fontMono }}>8項目</span>
              <span style={{ marginLeft: 'auto', fontSize: 11.5, fontWeight: 700, color: t.entity }}>7 / 8 合格</span>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
              {checks.map(([label, st]) => (
                <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 11.5, color: t.fg,
                  padding: '5px 8px', borderRadius: t.radiusSm, background: t.surfaceAlt }}>
                  <span style={{ width: 16, height: 16, borderRadius: 999, flex: '0 0 auto', display: 'flex', alignItems: 'center', justifyContent: 'center',
                    background: cColor[st], color: '#fff', fontSize: 10 }}>{glyph[st]}</span>
                  {label}
                </div>
              ))}
            </div>
          </Card>

          <Card t={t} pad={16} style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
            <h3 style={{ margin: '0 0 4px', fontSize: 13.5, fontWeight: 700 }}>ここを直したい</h3>
            <p style={{ margin: '0 0 9px', fontSize: 11.5, color: t.muted }}>コメントすると、AI が設計を作り直します（任意）。</p>
            <div style={{ flex: 1, borderRadius: t.radiusSm, border: `1px solid ${t.borderStrong}`, background: t.surfaceAlt,
              padding: '9px 11px', fontSize: 12, color: t.faint, minHeight: 54 }}>
              例：試料のIDを (SID, sample_id) の複合キーにして。理由も一緒に直して。
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
              <Btn t={t} kind="ghost" size="sm" icon="spark">作り直す</Btn>
              <span style={{ flex: 1 }} />
              <Btn t={t} kind="primary" icon="check">確認した — 保存へ</Btn>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { ScreenAdd });
