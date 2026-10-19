// 見本のデータ（例。件数は Starrydata 本番の規模感に合わせた仮の数）
// 星座 = データセット、星 = 種類、共通の名前 = 上の帯、標準の語 = 地平線、つながり = ハブ
window.SKY_DATA = {
  datasets: [
    {
      id: 'ds:starrydata-papers', label: '論文（Starrydata）',
      kinds: [
        { id: 'k:sd/paper', label: '論文', count: 8120, links: ['k:sd/author'], standard: 's:schema-article' },
        { id: 'k:sd/author', label: '著者', count: 21400, links: [] },
      ],
    },
    {
      id: 'ds:starrydata-samples', label: '試料（Starrydata）',
      kinds: [
        { id: 'k:sd/sample', label: '試料', count: 46200, links: ['k:sd/project'] },
        { id: 'k:sd/project', label: 'プロジェクト', count: 12, links: [] },
      ],
    },
    {
      id: 'ds:starrydata-curves', label: '測定曲線（Starrydata）',
      kinds: [
        { id: 'k:sd/curve', label: '測定曲線', count: 238000, links: ['k:sd/property', 'k:sd/unit'] },
        { id: 'k:sd/property', label: '物性', count: 38, links: ['k:sd/unit'], standard: 's:qudt-qk' },
        { id: 'k:sd/unit', label: '単位', count: 61, links: [], standard: 's:qudt-unit' },
      ],
    },
    {
      id: 'ds:xrd-experiment', label: 'XRD 実験',
      kinds: [
        { id: 'k:xrd/card', label: 'Card', count: 1, links: ['k:xrd/record'] },
        { id: 'k:xrd/record', label: 'Record', count: 3001, links: [] },
      ],
    },
    {
      id: 'ds:xrd-cards', label: 'XRD 参照カード',
      kinds: [
        { id: 'k:xc/card', label: 'Card', count: 2, links: ['k:xc/record', 'k:xc/formula'] },
        { id: 'k:xc/record', label: 'Record', count: 47, links: [] },
        { id: 'k:xc/formula', label: '化学式', count: 2, links: [] },
      ],
    },
  ],
  names: [
    { id: 'n:sample', label: '試料', kind: 'class', members: ['k:sd/sample'], standard: 's:cmso-material', relation: 'sub', cqs: ['cq:zt-max', 'cq:paper-samples'] },
    { id: 'n:card', label: 'カード', kind: 'class', members: ['k:xrd/card', 'k:xc/card'], standard: null, relation: 'sub', cqs: ['cq:cards'] },
    { id: 'n:diffraction-point', label: '回折点', kind: 'class', members: ['k:xrd/record', 'k:xc/record'], standard: null, relation: 'sub', cqs: ['cq:diff-points'] },
    { id: 'n:composition', label: '組成', kind: 'property', members: ['k:sd/sample', 'k:xc/formula'], standard: null, relation: 'sub', cqs: ['cq:zt-max', 'cq:snse'] },
    { id: 'n:battery-cell', label: '電池セル', kind: 'class', members: [], standard: null, relation: 'sub', cqs: ['cq:cell-capacity'] },
  ],
  standards: [
    { id: 's:schema-article', label: 'ScholarlyArticle', prefix: 'schema' },
    { id: 's:cmso-material', label: 'Material', prefix: 'cmso' },
    { id: 's:qudt-qk', label: 'QuantityKind', prefix: 'qudt' },
    { id: 's:qudt-unit', label: 'Unit', prefix: 'qudt' },
  ],
  hubs: [
    { id: 'h:composition', label: 'つながり: 組成', members: ['k:sd/sample', 'k:xc/formula'] },
  ],
  cqs: [
    { id: 'cq:zt-max', ledger: '熱電材料', title: 'ZT が最も高い試料の組成は？', answering: 1, needs: ['n:sample', 'n:composition'], shape: '上位の値 × 試料 × ZT' },
    { id: 'cq:snse', ledger: '熱電材料', title: 'SnSe を含む組成の試料は？', answering: 1, needs: ['n:composition'], shape: '値で絞る × 試料 × 組成' },
    { id: 'cq:paper-samples', ledger: '熱電材料', title: 'この論文が報告した試料は？', answering: 1, needs: ['n:sample'], shape: '一覧 × 試料 × 論文' },
    { id: 'cq:seebeck-temp', ledger: '熱電材料', title: '組成 X の Seebeck 係数の温度依存は？', answering: 0, needs: ['n:composition'], shape: '推移 × 測定曲線 × 温度' },
    { id: 'cq:cards', ledger: 'XRD', title: 'カードは、どのデータにいくつあるか', answering: 2, needs: ['n:card'], shape: '件数 × カード' },
    { id: 'cq:diff-points', ledger: 'XRD', title: '回折点は、どのデータにいくつあるか', answering: 2, needs: ['n:diffraction-point'], shape: '件数 × 回折点' },
    { id: 'cq:cell-capacity', ledger: '電池', title: '容量が最も大きいセルの正極材料は？', answering: 0, needs: ['n:battery-cell'], shape: '上位の値 × 電池セル × 容量' },
  ],
}
