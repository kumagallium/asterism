// 作業場の文（名前・関係の文・差分 1 操作の文）。i18n の t を受け取る純関数（描画から切り離して固定する）。
// サーバが組む文（提案の sentences）とは別に、**UI が組むのは次の 2 つだけ**:
//   - 人の直接編集の記録（プルダウン・ドラッグ・手で足す）
//   - 保存された会話の復元（サーバは文を保存しない）
import type { GraphOp } from './consultGraphApi'
import { type ThemeDraft, conceptName } from './themeDraft'

export type TFn = (key: string, opts?: Record<string, unknown>) => string

/** 概念の表示名。標準の語は「…（標準）」。key が下書きに無ければ key のまま。 */
export function nameOfKey(draft: Pick<ThemeDraft, 'concepts' | 'standards'>, key: string, lang: string, t: TFn): string {
  const row = draft.concepts.find((r) => r.key === key)
  if (row) return conceptName(row, lang)
  const std = draft.standards[key]
  if (std) return t('themes:rel.stdName', { name: std.label || key.slice(key.indexOf(':') + 1) })
  const i = key.indexOf(':')
  return i > 0 ? t('themes:rel.stdName', { name: key.slice(i + 1) }) : key
}

export function relationSentenceOf(
  draft: Pick<ThemeDraft, 'concepts' | 'standards'>,
  rel: { from: string; rel: string; to: string },
  lang: string,
  t: TFn,
): string {
  return t(`themes:rel.sentence.${rel.rel}`, {
    subject: nameOfKey(draft, rel.from, lang, t),
    object: nameOfKey(draft, rel.to, lang, t),
  })
}

const s = (v: unknown): string => (typeof v === 'string' ? v : '')

/** 差分 1 操作の 1 文（人の直接編集の記録・会話の復元用）。 */
export function opSentence(draft: Pick<ThemeDraft, 'concepts' | 'standards'>, op: GraphOp, lang: string, t: TFn): string {
  const nm = (key: string) => nameOfKey(draft, key, lang, t)
  switch (op.op) {
    case 'add_concept': {
      const name = lang.startsWith('en') ? s(op.label_en) || s(op.label_ja) : s(op.label_ja)
      return t('themes:chat.op.add_concept', { name, kind: t(`themes:kind.${op.kind === 'property' ? 'property' : 'class'}`) })
    }
    case 'add_relation':
      return relationSentenceOf(draft, { from: s(op.from), rel: s(op.rel), to: s(op.to) }, lang, t)
    case 'retarget':
      return t('themes:chat.op.retarget', {
        subject: nm(s(op.from)),
        rel: t(`themes:map.pillTitle.${s(op.rel)}`),
        from: nm(s(op.to)),
        to: nm(s(op.new_to)),
      })
    case 'remove':
      return op.target === 'relation'
        ? t('themes:chat.op.remove_relation', {
            sentence: relationSentenceOf(draft, { from: s(op.from), rel: s(op.rel), to: s(op.to) }, lang, t),
          })
        : t('themes:chat.op.remove_concept', { name: nm(s(op.key)) })
    case 'rename':
      return t('themes:chat.op.rename', {
        name: nm(s(op.key)),
        to: lang.startsWith('en') ? s(op.label_en) || s(op.label_ja) : s(op.label_ja),
      })
    case 'add_question':
      return t('themes:chat.op.add_question', { question: s(op.question) })
    default:
      return s(op.op)
  }
}
