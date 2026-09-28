import { describe, expect, it } from 'vitest'
import { parseMermaidFlowchart, toMermaidFlowchart } from './mermaidFlow'
import type { GraphSpec } from './viewSpec'
import mermaidCases from './fixtures/mermaid_cases.json'

/** 受け付ける構文の部分集合だけを読めること・部分集合の外は行ごと捨てること・
 *  GraphSpec → Mermaid → GraphSpec の往復で意味が保たれることを確かめる。
 *  題材は図書館の貸出と気象観測（分野語を書かないため・2 分野以上）。 */

describe('parseMermaidFlowchart: 向き', () => {
  it('ヘッダが無ければ既定は LR', () => {
    const { graph } = parseMermaidFlowchart('book --> patron')
    expect(graph.direction).toBe('LR')
  })

  it('graph TD をそのまま読む', () => {
    const { graph } = parseMermaidFlowchart('graph TD\nbook --> patron')
    expect(graph.direction).toBe('TD')
  })

  it('flowchart RL は LR に正規化する', () => {
    const { graph } = parseMermaidFlowchart('flowchart RL\nbook --> patron')
    expect(graph.direction).toBe('LR')
  })

  it('flowchart BT は TD に正規化する', () => {
    const { graph } = parseMermaidFlowchart('flowchart BT\nbook --> patron')
    expect(graph.direction).toBe('TD')
  })
})

describe('parseMermaidFlowchart: 節の形', () => {
  it.each([
    ['book[Book]', 'Book'],
    ['book(Book)', 'Book'],
    ['book([Book])', 'Book'],
    ['book{Book}', 'Book'],
    ['book[[Book]]', 'Book'],
    ['book((Book))', 'Book'],
  ])('%s → ラベル %s', (line, label) => {
    const { graph } = parseMermaidFlowchart(`graph LR\n${line}`)
    expect(graph.nodes).toEqual([{ id: 'book', label, kind: 'entity' }])
  })

  it('ラベルは "..." で囲まれていてもよい', () => {
    const { graph } = parseMermaidFlowchart('graph LR\nbook["Weekly Loan"]')
    expect(graph.nodes[0].label).toBe('Weekly Loan')
  })

  it('ラベルの無い裸の id は id がそのままラベルになる', () => {
    const { graph } = parseMermaidFlowchart('graph LR\nstation')
    expect(graph.nodes[0]).toEqual({ id: 'station', label: 'station', kind: 'entity' })
  })
})

describe('parseMermaidFlowchart: 辺', () => {
  it('A --> B', () => {
    const { graph } = parseMermaidFlowchart('graph LR\nstation --> sensor')
    expect(graph.edges).toEqual([{ from: 'station', to: 'sensor', label: undefined }])
  })

  it('A --- B（ラベル無し）', () => {
    const { graph } = parseMermaidFlowchart('graph LR\nstation --- sensor')
    expect(graph.edges).toEqual([{ from: 'station', to: 'sensor', label: undefined }])
  })

  it('A -- text --> B（矢印の間のラベル）', () => {
    const { graph } = parseMermaidFlowchart('graph LR\npatron -- borrows --> book')
    expect(graph.edges).toEqual([{ from: 'patron', to: 'book', label: 'borrows' }])
  })

  it('A -->|text| B（パイプのラベル）', () => {
    const { graph } = parseMermaidFlowchart('graph LR\ndesk -->|logs| archive')
    expect(graph.edges).toEqual([{ from: 'desk', to: 'archive', label: 'logs' }])
  })

  it('A -.-> B（点線）', () => {
    const { graph } = parseMermaidFlowchart('graph LR\narchive -.-> kiosk')
    expect(graph.edges).toEqual([{ from: 'archive', to: 'kiosk', label: undefined }])
  })

  it('A ==> B（太線）', () => {
    const { graph } = parseMermaidFlowchart('graph LR\nkiosk ==> counter')
    expect(graph.edges).toEqual([{ from: 'kiosk', to: 'counter', label: undefined }])
  })

  it('1 行の鎖 A --> B --> C は 2 本の辺になる', () => {
    const { graph } = parseMermaidFlowchart('graph LR\nstation --> sensor --> reading')
    expect(graph.nodes.map((n) => n.id)).toEqual(['station', 'sensor', 'reading'])
    expect(graph.edges).toEqual([
      { from: 'station', to: 'sensor', label: undefined },
      { from: 'sensor', to: 'reading', label: undefined },
    ])
  })
})

describe('parseMermaidFlowchart: 種類（class / classDef / :::）', () => {
  it(':::activity で節の種類を付ける', () => {
    const { graph } = parseMermaidFlowchart('graph LR\ndesk[Front Desk]:::activity')
    expect(graph.nodes[0].kind).toBe('activity')
  })

  it('class 文で複数の節に一度に種類を付ける', () => {
    const { graph } = parseMermaidFlowchart(
      'graph LR\nstation[Station]\nsensor[Sensor]\nclass station,sensor activity',
    )
    expect(graph.nodes.map((n) => n.kind)).toEqual(['activity', 'activity'])
  })

  it('classDef は色を読んで捨てるだけで節には影響しない', () => {
    const { graph } = parseMermaidFlowchart(
      'graph LR\nclassDef entity fill:#eee,stroke:#333\nbook[Book]:::entity',
    )
    expect(graph.nodes[0].kind).toBe('entity')
  })

  it('entity / activity 以外のクラス名は other になる', () => {
    const { graph } = parseMermaidFlowchart('graph LR\nkiosk[Kiosk]:::note')
    expect(graph.nodes[0].kind).toBe('other')
  })

  it('クラス指定が無い節は既定で entity', () => {
    const { graph } = parseMermaidFlowchart('graph LR\nbook[Book]')
    expect(graph.nodes[0].kind).toBe('entity')
  })
})

describe('parseMermaidFlowchart: subgraph', () => {
  it('subgraph の中の節に group を付け、枠自体は捨てる', () => {
    const { graph, dropped } = parseMermaidFlowchart(
      'graph LR\nsubgraph Circulation\nbook[Book] --> patron[Patron]\nend',
    )
    expect(graph.nodes.map((n) => n.props?.group)).toEqual(['Circulation', 'Circulation'])
    expect(dropped).toEqual([])
  })

  it('subgraph の外の節には group が付かない', () => {
    const { graph } = parseMermaidFlowchart(
      'graph LR\narchive[Archive]\nsubgraph Circulation\nbook[Book]\nend',
    )
    expect(graph.nodes[0].props).toBeUndefined()
  })
})

describe('parseMermaidFlowchart: コメント・空行・CRLF', () => {
  it('%% コメント行と空行を無視する', () => {
    const { graph } = parseMermaidFlowchart(
      'graph LR\n%% これはコメント\n\nstation --> sensor\n',
    )
    expect(graph.nodes.map((n) => n.id)).toEqual(['station', 'sensor'])
  })

  it('CRLF を LF として読む', () => {
    const { graph } = parseMermaidFlowchart('graph LR\r\nstation --> sensor\r\n')
    expect(graph.edges).toEqual([{ from: 'station', to: 'sensor', label: undefined }])
  })
})

describe('parseMermaidFlowchart: 部分集合の外は行ごと捨てる', () => {
  it.each([
    'click desk "https://example.org"',
    'style book fill:#fff',
    'linkStyle 0 stroke:#333',
    'direction TB',
    'patron <--> desk',
  ])('%s は dropped に入る', (line) => {
    const { dropped } = parseMermaidFlowchart(`graph LR\n${line}`)
    expect(dropped).toEqual([line])
  })

  it('読めない行があっても、読める行は普通に解釈する', () => {
    const { graph, dropped } = parseMermaidFlowchart(
      'graph LR\nstation --> sensor\nclick station "https://example.org"\nsensor --> reading',
    )
    expect(graph.edges).toEqual([
      { from: 'station', to: 'sensor', label: undefined },
      { from: 'sensor', to: 'reading', label: undefined },
    ])
    expect(dropped).toEqual(['click station "https://example.org"'])
  })
})

describe('toMermaidFlowchart / 往復', () => {
  const roundTrip = (g: GraphSpec) => parseMermaidFlowchart(toMermaidFlowchart(g)).graph

  it('direction・節の id/label/kind・辺が保たれる（図書館）', () => {
    const g: GraphSpec = {
      direction: 'LR',
      nodes: [
        { id: 'book', label: 'Book', kind: 'entity' },
        { id: 'patron', label: 'Patron', kind: 'entity' },
        { id: 'desk', label: 'Front Desk', kind: 'activity' },
      ],
      edges: [
        { from: 'patron', to: 'desk', label: 'borrows' },
        { from: 'desk', to: 'book' },
      ],
    }
    const back = roundTrip(g)
    expect(back.direction).toBe(g.direction)
    expect(back.nodes.map((n) => ({ id: n.id, label: n.label, kind: n.kind }))).toEqual(
      g.nodes.map((n) => ({ id: n.id, label: n.label, kind: n.kind })),
    )
    expect(back.edges).toEqual(g.edges.map((e) => ({ from: e.from, to: e.to, label: e.label })))
  })

  it('direction・節の id/label/kind・辺が保たれる（気象観測、TD）', () => {
    const g: GraphSpec = {
      direction: 'TD',
      nodes: [
        { id: 'station', label: 'Station', kind: 'entity' },
        { id: 'sensor', label: 'Sensor', kind: 'other' },
        { id: 'reading', label: 'Reading', kind: 'activity' },
      ],
      edges: [
        { from: 'station', to: 'sensor' },
        { from: 'sensor', to: 'reading', label: 'observes' },
      ],
    }
    const back = roundTrip(g)
    expect(back.direction).toBe(g.direction)
    expect(back.nodes.map((n) => ({ id: n.id, label: n.label, kind: n.kind }))).toEqual(
      g.nodes.map((n) => ({ id: n.id, label: n.label, kind: n.kind })),
    )
    expect(back.edges).toEqual(g.edges.map((e) => ({ from: e.from, to: e.to, label: e.label })))
  })

  it('ラベルの中の " は #quot; を経由して往復する', () => {
    const g: GraphSpec = {
      nodes: [{ id: 'book', label: 'The "Weekly" Loan', kind: 'entity' }],
      edges: [],
    }
    const text = toMermaidFlowchart(g)
    expect(text).toContain('#quot;')
    expect(roundTrip(g).nodes[0].label).toBe('The "Weekly" Loan')
  })

  it.each([['a --> b'], ['a --- b'], ['a ==> b'], ['a -.-> b'], ['a -->|x| b']])(
    'ラベルに矢印記号列 %s を含む節が往復する',
    (label) => {
      const g: GraphSpec = {
        nodes: [
          { id: 'book', label, kind: 'entity' },
          { id: 'patron', label: 'Patron', kind: 'entity' },
        ],
        edges: [{ from: 'book', to: 'patron' }],
      }
      const back = roundTrip(g)
      expect(back.nodes.map((n) => ({ id: n.id, label: n.label, kind: n.kind }))).toEqual(
        g.nodes.map((n) => ({ id: n.id, label: n.label, kind: n.kind })),
      )
      expect(back.edges).toEqual(g.edges.map((e) => ({ from: e.from, to: e.to, label: e.label })))
    },
  )
})

describe('parseMermaidFlowchart: 引用ラベル内の矢印と本物の辺の混在', () => {
  it('同じ行にラベル内の矢印記号と本物の辺が混在しても、本物の辺だけを読む', () => {
    const { graph, dropped } = parseMermaidFlowchart(
      'graph LR\nbook["a --> b"] --> patron["c --- d"]',
    )
    expect(dropped).toEqual([])
    expect(graph.nodes).toEqual([
      { id: 'book', label: 'a --> b', kind: 'entity' },
      { id: 'patron', label: 'c --- d', kind: 'entity' },
    ])
    expect(graph.edges).toEqual([{ from: 'book', to: 'patron', label: undefined }])
  })
})

// 契約メモ §3（PR D・D1-export）: `ingest/src/asterism/mermaid_flow.to_mermaid`
// が ui の `toMermaidFlowchart` と同じ書式（graph LR / id["label"]:::kind /
// A -->|label| B）で書くことを、共有フィクスチャ（`fixtures/mermaid_cases.json`）
// で固定する（往路のみ — Python 側は parse を持たない）。
interface MermaidCase {
  name: string
  graph: GraphSpec
  expected: string
}

describe('toMermaidFlowchart: 共有フィクスチャ（ui/Python 一致・PR D §3）', () => {
  const cases = mermaidCases as MermaidCase[]

  it('フィクスチャが空でない（取り違え防止）', () => {
    expect(cases.length).toBeGreaterThan(0)
  })

  it.each(cases.map((c) => [c.name, c] as const))('%s', (_name, c) => {
    expect(toMermaidFlowchart(c.graph)).toBe(c.expected)
  })
})
