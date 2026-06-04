// context.jsx — explanatory boards stating the design reasoning up front:
// the new information architecture, the jargon→plain-language translation, and
// the gallery restructure. Rendered in a neutral light palette.

function ContextIA(t) {
  const oldNav = [
    'ワークベンチ（設計）', 'Ask（根拠付き回答）', 'Gallery（語彙・マッピング）', '取り込み履歴', 'SPARQL（上級）',
  ];
  const newNav = [
    ['ホーム', 'Home', '新設：今あるデータと次の一手'],
    ['データを追加', 'Add data', '＝旧ワークベンチ。流れを3ステップに'],
    ['質問する', 'Ask', '根拠つき回答（ほぼ踏襲・整理）'],
    ['カタログ', 'Catalog', '＝旧Gallery。データセット主役に再構成'],
    ['アクティビティ', 'Activity', '＝取り込み履歴'],
  ];
  return (
    <div style={{ height: '100%', background: t.surface, fontFamily: t.fontUI, color: t.fg, padding: '26px 30px', display: 'flex', flexDirection: 'column', gap: 18, overflow: 'hidden' }}>
      <div>
        <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.09em', textTransform: 'uppercase', color: t.accent }}>設計の前提 01</div>
        <h2 style={{ margin: '4px 0 0', fontFamily: t.fontDisplay, fontSize: 26, fontWeight: 700, letterSpacing: '-0.02em' }}>情報設計：ユーザーの頭の中に合わせる</h2>
        <p style={{ margin: '8px 0 0', fontSize: 13.5, color: t.muted, maxWidth: 720 }}>
          現状は「パイプラインの内部構造」がそのまま並びナビが難解。新案は<strong style={{ color: t.fg }}>3つの動詞</strong>＝
          <strong style={{ color: t.fg }}>入れる → 確かめる/問う → 見渡す</strong>に沿って、平易な言葉で並べ替えます。
        </p>
      </div>

      {/* mental model band */}
      <div style={{ display: 'flex', gap: 12 }}>
        {[['つくる', 'データを追加', 'add'], ['つかう', '質問する・カタログ', 'ask'], ['管理', 'アクティビティ', 'activity']].map(([k, v, ic]) => {
          const I = Icons[ic];
          return (
            <div key={k} style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 11, padding: '13px 15px', borderRadius: t.radiusSm, background: t.surfaceAlt, border: `1px solid ${t.border}` }}>
              <span style={{ width: 34, height: 34, borderRadius: t.radiusSm, background: t.primarySoft, color: t.primary, display: 'flex', alignItems: 'center', justifyContent: 'center' }}><I size={18} /></span>
              <div>
                <div style={{ fontSize: 11, color: t.faint, fontWeight: 700, letterSpacing: '0.06em' }}>{k}</div>
                <div style={{ fontSize: 13.5, fontWeight: 700 }}>{v}</div>
              </div>
            </div>
          );
        })}
      </div>

      {/* old → new */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr auto 1.4fr', gap: 24, alignItems: 'center', flex: 1, minHeight: 0 }}>
        <div>
          <div style={{ fontSize: 11.5, fontWeight: 700, color: t.faint, marginBottom: 9, textTransform: 'uppercase', letterSpacing: '0.06em' }}>現状（難しい）</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {oldNav.map((n) => (
              <div key={n} style={{ fontSize: 12.5, color: t.muted, padding: '8px 11px', borderRadius: t.radiusSm, background: t.surfaceAlt, border: `1px solid ${t.border}`, textDecoration: 'none' }}>{n}</div>
            ))}
          </div>
        </div>
        <div style={{ color: t.accent, display: 'flex', justifyContent: 'center' }}><Icons.arrow size={26} /></div>
        <div>
          <div style={{ fontSize: 11.5, fontWeight: 700, color: t.primary, marginBottom: 9, textTransform: 'uppercase', letterSpacing: '0.06em' }}>新案（平易・動詞で）</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {newNav.map(([ja, en, note]) => (
              <div key={ja} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', borderRadius: t.radiusSm, background: t.surface, border: `1px solid ${t.borderStrong}`, boxShadow: t.shadowSoft }}>
                <span style={{ fontSize: 13.5, fontWeight: 700, color: t.fg, minWidth: 108 }}>{ja}</span>
                <span style={{ fontSize: 10.5, fontFamily: t.fontMono, color: t.faint, minWidth: 64 }}>{en}</span>
                <span style={{ fontSize: 12, color: t.muted }}>{note}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function ContextTerms(t) {
  const terms = [
    ['RDF / 知識グラフ', 'つながったデータ', 'knowledge graph'],
    ['オントロジー / TBox', 'データの設計図（語彙）', 'vocabulary'],
    ['MIE / ingester / RML', '取り込みルール', 'mapping'],
    ['8つの罠 (traps)', '品質チェック（8項目）', 'validation'],
    ['materialize', '保存（確定）', 'save'],
    ['canonical / promote', '正式データに反映 ↔ 下書き', 'publish'],
    ['provenance / 来歴', '出どころ（来歴）', 'provenance'],
    ['citation', '根拠（引用）', 'citation'],
  ];
  return (
    <div style={{ height: '100%', background: t.surface, fontFamily: t.fontUI, color: t.fg, padding: '26px 30px', display: 'flex', flexDirection: 'column', gap: 16, overflow: 'hidden' }}>
      <div>
        <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.09em', textTransform: 'uppercase', color: t.accent }}>設計の前提 02</div>
        <h2 style={{ margin: '4px 0 0', fontFamily: t.fontDisplay, fontSize: 26, fontWeight: 700, letterSpacing: '-0.02em' }}>用語：かみ砕いて隠す（でも消さない）</h2>
        <p style={{ margin: '8px 0 0', fontSize: 13.5, color: t.muted, maxWidth: 720 }}>
          画面では<strong style={{ color: t.fg }}>日本語の平易な言葉</strong>を主役に。専門語は
          <span style={{ borderBottom: `1.5px dotted ${t.faint}` }}>点線</span>のヒントや英語併記で「知りたい人だけ」見えるように。
        </p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 28px', flex: 1, alignContent: 'start' }}>
        {terms.map(([oldT, newT, en]) => (
          <div key={oldT} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 0', borderBottom: `1px solid ${t.border}` }}>
            <span style={{ fontSize: 12.5, color: t.faint, textDecoration: 'line-through', minWidth: 150, fontFamily: t.fontMono }}>{oldT}</span>
            <Icons.arrow size={15} />
            <span style={{ fontSize: 14, fontWeight: 700, color: t.fg, flex: 1 }}>{newT}</span>
            <span style={{ fontSize: 10.5, fontFamily: t.fontMono, color: t.activity }}>{en}</span>
          </div>
        ))}
      </div>

      {/* gallery restructure note */}
      <div style={{ display: 'flex', gap: 16, padding: '16px 18px', borderRadius: t.radiusSm, background: t.surfaceAlt, border: `1px solid ${t.border}` }}>
        <span style={{ width: 36, height: 36, borderRadius: t.radiusSm, background: t.primarySoft, color: t.primary, display: 'flex', alignItems: 'center', justifyContent: 'center', flex: '0 0 auto' }}><Icons.catalog size={18} /></span>
        <div>
          <div style={{ fontSize: 13.5, fontWeight: 700, marginBottom: 4 }}>カタログ（旧Gallery）の作り直し</div>
          <div style={{ fontSize: 12.5, color: t.muted, lineHeight: 1.7 }}>
            旧：抽象的な「<span style={{ fontFamily: t.fontMono }}>Ontologies / Mappings</span>」を並列表示＋編集リスクの記号が散乱 → 構造が読めない。<br />
            新：<strong style={{ color: t.fg }}>データセットを主役</strong>に（＝ユーザーが所有する単位）。各カードは「<strong style={{ color: t.fg }}>答えられる問い</strong>」を見出しに。
            共通の<strong style={{ color: t.fg }}>「共有の語彙」</strong>は下に1か所だけ、平易な注意書きとともに置く。
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { ContextIA, ContextTerms });
