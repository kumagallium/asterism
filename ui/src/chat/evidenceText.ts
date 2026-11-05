// 根拠の文を札に出すときの整え方（純関数。部品は EvidenceLine.tsx）。
import type { TFunction } from 'i18next'
import type { Evidence } from './chatApi'

export const EVIDENCE_SHOWN = 150

/** 札に出す文（150 字で切って「…」）。 */
export const clipEvidence = (text: string): string => (text.length > EVIDENCE_SHOWN ? `${text.slice(0, EVIDENCE_SHOWN)}…` : text)

/**
 * 「論文名 · 節」。節は `section`（題名）が先、無ければ `path` から「節 n」、どちらも無ければ論文名だけ。
 * 論文名が無い（下書きの差分の根拠など）ときは出さない（空文字）。
 */
export function evidenceSource(e: Pick<Evidence, 'dataset_label' | 'section' | 'path'>, t: TFunction<'chat'>): string {
  if (!e.dataset_label) return ''
  const sec = e.section ? e.section : e.path ? t('chat:evidence.section', { n: e.path }) : ''
  return sec ? `${e.dataset_label} · ${sec}` : e.dataset_label
}
