// 空の状態（データ 0・問い 0）で薄く重ねる見本の星図。`docs/design/sky-chart-mock/data.js` の写し。
// 実在のデータではない（件数は本番の規模感に合わせた仮の数）。読み取り専用・何にも書き込まない。
import type { SkyData, SkyKind, SkyTerm } from './skyApi'

const kind = (iri: string, label: string, count: number, links: string[] = [], standard: string | null = null): SkyKind => ({
  iri: `sample:${iri}`,
  label,
  count,
  links: links.map((l) => `sample:${l}`),
  standard: standard ? `sample:${standard}` : null,
})

const term = (
  slug: string,
  label: string,
  labelEn: string,
  kindOf: 'class' | 'property',
  members: [string, string][],
  standard: string | null = null,
): SkyTerm => ({
  iri: `sample:${slug}`,
  slug,
  label,
  label_en: labelEn,
  kind: kindOf,
  members: members.map(([dataset_id, iri]) => ({
    iri: `sample:${iri}`,
    dataset_id,
    relation: kindOf === 'class' ? 'subClassOf' : 'subPropertyOf',
  })),
  standard: standard ? { iri: `sample:${standard}`, relation: 'equivalentClass' } : null,
  answering: 0,
  questions: [],
})

export const SAMPLE_SKY: SkyData = {
  datasets: [
    {
      id: 'papers',
      label: '論文',
      created_at: '2026-01-01T00:00:00Z',
      kinds: [kind('paper', '論文', 8120, ['author'], 'schema-article'), kind('author', '著者', 21400)],
    },
    {
      id: 'samples',
      label: '試料',
      created_at: '2026-01-02T00:00:00Z',
      kinds: [kind('sample', '試料', 46200, ['project']), kind('project', 'プロジェクト', 12)],
    },
    {
      id: 'curves',
      label: '測定曲線',
      created_at: '2026-01-03T00:00:00Z',
      kinds: [
        kind('curve', '測定曲線', 238000, ['property', 'unit']),
        kind('property', '物性', 38, ['unit'], 'qudt-qk'),
        kind('unit', '単位', 61, [], 'qudt-unit'),
      ],
    },
    {
      id: 'xrd',
      label: 'XRD 実験',
      created_at: '2026-01-04T00:00:00Z',
      kinds: [kind('xrd-card', 'Card', 1, ['xrd-record']), kind('xrd-record', 'Record', 3001)],
    },
    {
      id: 'xrd-cards',
      label: 'XRD 参照カード',
      created_at: '2026-01-05T00:00:00Z',
      kinds: [
        kind('xc-card', 'Card', 2, ['xc-record', 'xc-formula']),
        kind('xc-record', 'Record', 47),
        kind('xc-formula', '化学式', 2),
      ],
    },
  ],
  terms: [
    term('sample', '試料', 'Sample', 'class', [['samples', 'sample']], 'cmso-material'),
    term('card', 'カード', 'Card', 'class', [
      ['xrd', 'xrd-card'],
      ['xrd-cards', 'xc-card'],
    ]),
    term('diffraction-point', '回折点', 'Diffraction point', 'class', [
      ['xrd', 'xrd-record'],
      ['xrd-cards', 'xc-record'],
    ]),
    term('composition', '組成', 'Composition', 'property', [
      ['samples', 'sample'],
      ['xrd-cards', 'xc-formula'],
    ]),
    term('battery-cell', '電池セル', 'Battery cell', 'class', []),
  ],
  standards: [
    { iri: 'sample:schema-article', label: 'ScholarlyArticle', prefix: 'schema' },
    { iri: 'sample:cmso-material', label: 'Material', prefix: 'cmso' },
    { iri: 'sample:qudt-qk', label: 'QuantityKind', prefix: 'qudt' },
    { iri: 'sample:qudt-unit', label: 'Unit', prefix: 'qudt' },
  ],
  hubs: [{ id: 'composition', label: '組成', members: ['sample:sample', 'sample:xc-formula'] }],
  questions: [],
  facts: {
    triples: 0,
    datasets: 5,
    kinds: 12,
    terms: 5,
    questions_answerable: 0,
    questions_total: 0,
    published_graphs: 0,
  },
  recent: [],
  truncated: false,
}
