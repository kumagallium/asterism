// screen-extras.jsx — two supporting boards:
//   ScreenConnect — connecting a NON-CSV source (API), making the "any data
//     source" promise concrete: endpoint + auth + live schema preview + schedule.
//   ScreenStates  — the easy-to-forget states: loading (skeleton), empty, error.

function ScreenConnect(t) {
  const field = (label, value, mono, ph) => (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
      <span style={{ fontSize: 11.5, fontWeight: 700, color: t.muted }}>{label}</span>
      <span style={{ display: 'flex', alignItems: 'center', padding: '9px 12px', borderRadius: t.radiusSm, border: `1px solid ${t.borderStrong}`, background: t.surface,
        fontSize: 12.5, fontFamily: mono ? t.fontMono : t.fontUI, color: value ? t.fg : t.faint }}>{value || ph}</span>
    </label>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14, height: '100%' }}>
      {/* source picker — API selected */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px', borderRadius: t.radius, background: t.surfaceAlt, border: `1px solid ${t.border}` }}>
        <span style={{ fontSize: 11.5, fontWeight: 700, color: t.muted }}>データソース</span>
        <div style={{ display: 'flex', gap: 4, background: t.surface, border: `1px solid ${t.border}`, borderRadius: 999, padding: 3 }}>
          {[['表計算 / CSV', 'file', false], ['JSON', 'code', false], ['API', 'link', true], ['DB', 'layers', false]].map(([lbl, ic, on]) => {
            const I = Icons[ic];
            return <span key={lbl} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 600, padding: '5px 12px', borderRadius: 999, cursor: 'pointer', background: on ? t.primary : 'transparent', color: on ? t.primaryFg : t.muted }}><I size={14} /> {lbl}</span>;
          })}
        </div>
        <span style={{ fontSize: 11, color: t.faint }}>CSV と同じ流れ（接続 → AI設計 → 確認 → 保存）。違うのは入口だけ。</span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.1fr', gap: 16, flex: 1, minHeight: 0 }}>
        {/* connection form */}
        <Card t={t} pad={0} style={{ display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
          <div style={{ padding: '14px 18px', borderBottom: `1px solid ${t.border}`, display: 'flex', alignItems: 'center', gap: 9 }}>
            <span style={{ color: t.activity, display: 'flex' }}><Icons.link size={17} /></span>
            <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>API に接続</h3>
            <span style={{ fontSize: 11, fontFamily: t.fontMono, color: t.faint }}>REST / GraphQL</span>
          </div>
          <div style={{ padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 13, overflow: 'auto' }}>
            {field('エンドポイント URL', 'https://api.starrydata.org/v2/curves', true)}
            <div style={{ display: 'grid', gridTemplateColumns: '0.8fr 1.2fr', gap: 12 }}>
              <label style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                <span style={{ fontSize: 11.5, fontWeight: 700, color: t.muted }}>認証方式</span>
                <span style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '9px 12px', borderRadius: t.radiusSm, border: `1px solid ${t.borderStrong}`, background: t.surface, fontSize: 12.5, color: t.fg }}>
                  Bearer トークン <Icons.chevron size={13} />
                </span>
              </label>
              {field('APIキー', '•••••••••••••••••••• sk_live', true)}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px', borderRadius: t.radiusSm, background: t.entitySoft }}>
              <span style={{ width: 18, height: 18, borderRadius: 999, background: t.entity, color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, flex: '0 0 auto' }}>✓</span>
              <span style={{ fontSize: 12, color: t.fg }}>接続できました — <strong>342</strong> 件のサンプルを検出</span>
              <span style={{ marginLeft: 'auto', fontSize: 11, fontFamily: t.fontMono, color: t.muted }}>120ms</span>
            </div>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
              <span style={{ fontSize: 11.5, fontWeight: 700, color: t.muted }}>取得タイミング</span>
              <div style={{ display: 'flex', gap: 6 }}>
                {['一度だけ', '毎日', '毎週', '手動'].map((o, i) => (
                  <span key={o} style={{ fontSize: 12, fontWeight: 600, padding: '6px 13px', borderRadius: 999, cursor: 'pointer',
                    background: i === 1 ? t.primarySoft : t.surfaceAlt, color: i === 1 ? t.primary : t.muted, border: `1px solid ${i === 1 ? 'transparent' : t.border}` }}>{o}</span>
                ))}
              </div>
            </label>
          </div>
          <div style={{ marginTop: 'auto', padding: '13px 18px', borderTop: `1px solid ${t.border}`, display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 11.5, color: t.muted }}>キーは暗号化して保存されます</span>
            <span style={{ marginLeft: 'auto' }}><Btn t={t} kind="primary" icon="arrow">AI 設計へ進む</Btn></span>
          </div>
        </Card>

        {/* live schema preview */}
        <Card t={t} pad={0} style={{ display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
          <div style={{ padding: '14px 18px', borderBottom: `1px solid ${t.border}`, display: 'flex', alignItems: 'center', gap: 9 }}>
            <span style={{ color: t.muted, display: 'flex' }}><Icons.search size={16} /></span>
            <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>取得サンプル（先頭1件）</h3>
            <span style={{ fontSize: 9.5, fontFamily: t.fontMono, color: t.faint }}>AI不要・そのまま表示</span>
          </div>
          <div style={{ padding: '14px 18px', overflow: 'auto', flex: 1 }}>
            <pre style={{ margin: 0, fontSize: 11.5, fontFamily: t.fontMono, color: t.fg, lineHeight: 1.7, whiteSpace: 'pre-wrap' }}>
{`{
  "sample_id": "SD_88421",
  "composition": "SnSe",
  "temperature_K": 773,
  "ZT": 2.62,
  "seebeck_uV_K": 184.0,
  "doi": "10.1038/nature19...",
}`}
            </pre>
            <div style={{ marginTop: 14, fontSize: 11.5, fontWeight: 700, color: t.muted, marginBottom: 7 }}>検出されたフィールド <span style={{ fontFamily: t.fontMono, color: t.faint }}>6</span></div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {['sample_id', 'composition', 'temperature_K', 'ZT', 'seebeck_uV_K', 'doi'].map((f) => (
                <code key={f} style={{ fontSize: 11, fontFamily: t.fontMono, color: t.fg, background: t.surfaceAlt, border: `1px solid ${t.border}`, padding: '3px 8px', borderRadius: 20 }}>{f}</code>
              ))}
            </div>
            <div style={{ marginTop: 14, display: 'flex', alignItems: 'center', gap: 9, fontSize: 11.5, color: t.muted, padding: '10px 12px', borderRadius: t.radiusSm, background: t.surfaceAlt, border: `1px dashed ${t.borderStrong}` }}>
              <Icons.spark size={15} />
              次のステップで AI が、この構造を既存の<strong style={{ color: t.fg }}>共有語彙</strong>に自動でつなぎます。
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}

// loading / empty / error — shown side by side so the states aren't forgotten.
function ScreenStates(t) {
  const skel = (w) => <span style={{ display: 'block', height: 11, borderRadius: 6, width: w, background: `linear-gradient(90deg, ${t.surfaceAlt} 25%, ${t.border} 37%, ${t.surfaceAlt} 63%)` }} />;
  const panel = (title, children) => (
    <Card t={t} pad={0} style={{ display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
      <div style={{ padding: '12px 16px', borderBottom: `1px solid ${t.border}`, fontSize: 12.5, fontWeight: 700, color: t.fg }}>{title}</div>
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '20px', textAlign: 'center', gap: 12 }}>{children}</div>
    </Card>
  );

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16, height: '100%' }}>
      {/* loading */}
      {panel('読み込み中', (
        <div style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: 14 }}>
          {[0, 1, 2].map((i) => (
            <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: '12px', borderRadius: t.radiusSm, border: `1px solid ${t.border}`, alignItems: 'flex-start' }}>
              {skel('70%')}{skel('92%')}{skel('40%')}
            </div>
          ))}
          <div style={{ fontSize: 11.5, color: t.muted, display: 'flex', alignItems: 'center', gap: 7, justifyContent: 'center' }}>
            <span style={{ width: 13, height: 13, borderRadius: 999, border: `2px solid ${t.border}`, borderTopColor: t.primary, display: 'inline-block' }} />
            根拠を集めています…
          </div>
        </div>
      ))}

      {/* empty */}
      {panel('まだ何もない（はじめて）', (
        <React.Fragment>
          <span style={{ width: 54, height: 54, borderRadius: t.radiusLg, background: t.primarySoft, color: t.primary, display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Icons.add size={26} /></span>
          <div style={{ fontSize: 14, fontWeight: 700, color: t.fg }}>最初のデータを追加しましょう</div>
          <div style={{ fontSize: 12, color: t.muted, maxWidth: 220, lineHeight: 1.6 }}>CSV・API・JSON など、手元のデータから AI が知識グラフの下書きを作ります。</div>
          <Btn t={t} kind="primary" icon="add">データを追加</Btn>
          <span style={{ fontSize: 11, color: t.faint, cursor: 'pointer' }}>サンプルデータで試す →</span>
        </React.Fragment>
      ))}

      {/* error */}
      {panel('うまくいかなかった', (
        <React.Fragment>
          <span style={{ width: 54, height: 54, borderRadius: t.radiusLg, background: '#f6e3df', color: '#b4453a', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 26, fontWeight: 700 }}>!</span>
          <div style={{ fontSize: 14, fontWeight: 700, color: t.fg }}>接続がタイムアウトしました</div>
          <div style={{ fontSize: 12, color: t.muted, maxWidth: 230, lineHeight: 1.6 }}>API が 30 秒以内に応答しませんでした。キーや URL を確認するか、もう一度お試しください。</div>
          <div style={{ fontFamily: t.fontMono, fontSize: 10.5, color: t.faint, background: t.surfaceAlt, padding: '5px 9px', borderRadius: 6, border: `1px solid ${t.border}` }}>ETIMEDOUT · req_8f21c</div>
          <div style={{ display: 'flex', gap: 8 }}>
            <Btn t={t} kind="ghost" size="sm">設定を見直す</Btn>
            <Btn t={t} kind="primary" size="sm" icon="activity">再試行</Btn>
          </div>
        </React.Fragment>
      ))}
    </div>
  );
}

Object.assign(window, { ScreenConnect, ScreenStates });
