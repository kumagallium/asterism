// 論文の根拠の文を、関係の行の下に小さく出す共通の部品（12b の返事の札・12c の「文」のタブ）。
// 引用符つき・札では 150 字で切って「…」・title に全文。論文名と節があれば「論文名 · 節」を添える。
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Evidence } from './chatApi'
import { clipEvidence, evidenceSource } from './evidenceText'
import { useOpenPaper } from './paperReaderContext'

/** 根拠の文 1 件。`dataset_id` があって公開済みなら、押すと論文の本文が開く（13b）。 */
function EvidenceItem({ e }: { e: Evidence }) {
  const { t } = useTranslation('chat')
  const open = useOpenPaper(e.dataset_id)
  const src = evidenceSource(e, t)
  const quote = (
    <q className="chat-evidence-text" title={`${t('reply.evidence')}: ${e.full ?? e.text}`}>
      {clipEvidence(e.text)}
    </q>
  )
  return (
    <div className="chat-evidence-item">
      {open ? (
        <button type="button" className="link-btn chat-evidence-open" data-testid="evidence-open" title={t('reader.openSentence')} onClick={() => open(e.iri)}>
          {quote}
        </button>
      ) : (
        quote
      )}
      {src && (
        <span className="chat-evidence-src" data-testid="evidence-source">
          {src}
        </span>
      )}
    </div>
  )
}

/**
 * 根拠の文。`collapse` なら 2 件以上のとき 1 件目だけ出し「ほか n 件」（押すと開く・開いた状態は部品ごとのローカル state）。
 */
export function EvidenceLine({ evidence, collapse }: { evidence: Evidence[]; collapse?: boolean }) {
  const { t } = useTranslation('chat')
  const [open, setOpen] = useState(false)
  if (evidence.length === 0) return null
  // 13a: 根拠が 2 本以上の論文にまたがるときだけ「論文 k 本」を添える（論文が分からない文は数えない）
  const papers = new Set(evidence.map((e) => e.dataset_id || e.dataset_label || '').filter(Boolean)).size
  const hidden = collapse && !open ? evidence.length - 1 : 0
  const shown = hidden > 0 ? evidence.slice(0, 1) : evidence
  return (
    <div className="chat-evidence" data-testid="reply-evidence">
      {shown.map((e) => (
        <EvidenceItem key={e.iri} e={e} />
      ))}
      {papers >= 2 && (
        <span className="chat-evidence-src" data-testid="evidence-papers">
          {t('evidence.papers', { n: papers })}
        </span>
      )}
      {hidden > 0 && (
        <button type="button" className="link-btn chat-evidence-more" data-testid="evidence-more" onClick={() => setOpen(true)}>
          {t('evidence.more', { n: hidden })}
        </button>
      )}
    </div>
  )
}
