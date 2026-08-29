import { describe, expect, it } from 'vitest'
import { rulesMermaid } from './skeletonDiagram'
import type { DatasetRules, RuleMap } from './galleryApi'

/** ⑤で見せる「できあがった形」は、AI が §1 に書いた散文ではなく**保存済みの
 *  取り込みルール**から組む。だからここで確かめるのは「ルールにこう書いてあれば
 *  線が引かれる／引かれない」だけ。 */

const NS = 'https://example.org/xrd/ontology#'

const map = (id: string, over: Partial<RuleMap> = {}): RuleMap => ({
  id,
  subject: { template: `xrdr:${id}/{No}`, classes: [`xrd:${id}`], class_iris: [`${NS}${id}`] },
  properties: [],
  ...over,
})

const rules = (maps: RuleMap[], labels: Record<string, string> = {}): DatasetRules => ({
  maps,
  prefixes: {},
  warnings: [],
  labels,
})

describe('rulesMermaid', () => {
  it('draws one box per map, named by the class label when there is one', () => {
    const out = rulesMermaid(rules([map('Peak'), map('Crystal')], { [`${NS}Peak`]: 'ピーク' }))
    expect(out.split('\n')[0]).toBe('flowchart TD')
    expect(out).toContain('Peak["ピーク"]')
    // ラベルが無ければクラス名にそのまま落ちる（マップ id は最後の手段）。
    expect(out).toContain('Crystal["Crystal"]')
  })

  it('links two maps when one points at the other by template', () => {
    const peak = map('Peak', {
      properties: [
        {
          predicate: 'xrd:ofCrystal',
          predicate_iri: `${NS}ofCrystal`,
          label: '結晶',
          kind: 'template',
          template: 'xrdr:Crystal/{No}',
        },
      ],
    })
    const crystal = map('Crystal', {
      subject: { template: 'xrdr:Crystal/{No}', classes: ['xrd:Crystal'] },
    })
    expect(rulesMermaid(rules([peak, crystal]))).toContain('Peak -->|結晶| Crystal')
  })

  it('links by a join (parent_map) and by a shared constant IRI', () => {
    const joined = map('Peak', {
      properties: [
        {
          predicate: 'xrd:ofCrystal',
          predicate_iri: `${NS}ofCrystal`,
          kind: 'join',
          parent_map: 'Crystal',
        },
      ],
    })
    // ラベルが無い行はプレディケートの局所名を線の名前にする。
    expect(rulesMermaid(rules([joined, map('Crystal')]))).toContain('Peak -->|ofCrystal| Crystal')

    const doc = map('Doc', {
      subject: { constant: 'https://example.org/doc/1', constant_is_iri: true },
    })
    const cite = map('Peak', {
      properties: [
        {
          predicate: 'prov:wasDerivedFrom',
          predicate_iri: 'http://www.w3.org/ns/prov#wasDerivedFrom',
          kind: 'constant',
          constant: 'https://example.org/doc/1',
          constant_is_iri: true,
        },
      ],
    })
    expect(rulesMermaid(rules([cite, doc]))).toContain('Peak -->|wasDerivedFrom| Doc')
  })

  it('never draws a value as a line, and never draws the same pair twice', () => {
    const peak = map('Peak', {
      properties: [
        // ただの値。形ではないので線にしない。
        { predicate: 'xrd:dSpacing', predicate_iri: `${NS}dSpacing`, kind: 'reference', reference: 'd' },
        // 同じ相手を 2 度指しても線は 1 本。
        {
          predicate: 'xrd:ofCrystal',
          predicate_iri: `${NS}ofCrystal`,
          kind: 'template',
          template: 'xrdr:Crystal/{No}',
        },
        {
          predicate: 'xrd:alsoCrystal',
          predicate_iri: `${NS}alsoCrystal`,
          kind: 'template',
          template: 'xrdr:Crystal/{No}',
        },
      ],
    })
    const crystal = map('Crystal', {
      subject: { template: 'xrdr:Crystal/{No}', classes: ['xrd:Crystal'] },
    })
    const lines = rulesMermaid(rules([peak, crystal])).split('\n')
    expect(lines.filter((l) => l.includes('-->'))).toHaveLength(1)
    expect(lines.join('\n')).not.toContain('dSpacing')
  })

  it('keeps mermaid parseable when a label carries quotes or a pipe', () => {
    const peak = map('Peak', {
      properties: [
        {
          predicate: 'xrd:ofCrystal',
          predicate_iri: `${NS}ofCrystal`,
          label: 'a|b"c',
          kind: 'template',
          template: 'xrdr:Crystal/{No}',
        },
      ],
    })
    const crystal = map('Crystal', {
      subject: { template: 'xrdr:Crystal/{No}', classes: ['xrd:Crystal'] },
    })
    const out = rulesMermaid(rules([peak, crystal], { [`${NS}Peak`]: 'say "hi"' }))
    expect(out).toContain('Peak -->|a b c| Crystal')
    expect(out).toContain("Peak[\"say 'hi'\"]")
  })

  it('makes a mermaid-safe node id out of a name that is not an identifier', () => {
    const out = rulesMermaid(rules([map('化合物 A'), map('化合物-A')]))
    expect(out).not.toMatch(/^ {2}[^[\s]*[\s-][^[\s]*\[/m)
    // 潰れて同じになる 2 つの名前でも、箱は 2 つのまま。
    expect(out.split('\n').filter((l) => l.includes('['))).toHaveLength(2)
  })
})
