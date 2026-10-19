// 空の状態（データ 0・問い 0）で薄く重ねる見本の星図（熱電材料と献立）。`docs/design/sky-chart-mock/data.js` の写し。
// 実在のデータではない（件数は規模感に合わせた仮の数）。読み取り専用・何にも書き込まない。
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
      id: 'menu',
      label: '献立',
      created_at: '2026-01-04T00:00:00Z',
      kinds: [kind('menu-recipe', 'レシピ', 320, ['menu-ingredient']), kind('menu-ingredient', '食材', 480)],
    },
    {
      id: 'pantry',
      label: '買い物メモ',
      created_at: '2026-01-05T00:00:00Z',
      kinds: [
        kind('pantry-recipe', 'レシピ', 12, ['pantry-ingredient', 'pantry-amount']),
        kind('pantry-ingredient', '食材', 95),
        kind('pantry-amount', '分量', 2),
      ],
    },
  ],
  terms: [
    term('sample', '試料', 'Sample', 'class', [['samples', 'sample']], 'cmso-material'),
    term('recipe', 'レシピ', 'Recipe', 'class', [
      ['menu', 'menu-recipe'],
      ['pantry', 'pantry-recipe'],
    ]),
    term('ingredient', '食材', 'Ingredient', 'class', [
      ['menu', 'menu-ingredient'],
      ['pantry', 'pantry-ingredient'],
    ]),
    term('composition', '組成', 'Composition', 'property', [
      ['samples', 'sample'],
      ['pantry', 'pantry-amount'],
    ]),
    term('cooking-method', '調理法', 'Cooking method', 'class', []),
  ],
  standards: [
    { iri: 'sample:schema-article', label: 'ScholarlyArticle', prefix: 'schema' },
    { iri: 'sample:cmso-material', label: 'Material', prefix: 'cmso' },
    { iri: 'sample:qudt-qk', label: 'QuantityKind', prefix: 'qudt' },
    { iri: 'sample:qudt-unit', label: 'Unit', prefix: 'qudt' },
  ],
  hubs: [{ id: 'composition', label: '組成', members: ['sample:sample', 'sample:pantry-amount'] }],
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
