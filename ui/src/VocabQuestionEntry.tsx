import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { VocabTermForm } from './VocabTermForm'
import './vocabTerms.css'

/**
 * 問いを書く入口（ADR upper-structure-shared-terms.md §2.5.1 (1)）。題を 1 文書いて、答えに要る
 * 語をその場で置く。保存は語と問いで 1 操作（題が最初の問いになる）。題だけが先にあって語が
 * 無いときは何も保存しない（語の無い問いは画面の下書きに留める・§2.3）。
 *
 * 題は Ask の「この問いに答えられるように、ことばをつなぐ」から `#/vocab?q=<題>` で持ち込まれる。
 * 解釈はしない（LLM を使わない）。題をそのまま入力欄に置くだけ。
 */
export function VocabQuestionEntry({
  initialQuestion = '',
  onChanged,
}: {
  initialQuestion?: string
  /** 語（と最初の問い）を置いたあと。親が版を進め、ほかの節が読み直す。この節自身は何も取得しない。 */
  onChanged?: () => void
}) {
  const { t } = useTranslation()
  const [question, setQuestion] = useState(initialQuestion)
  // 置くたびにフォームを空にするための鍵。題は残す（同じ問いに要る別の語を続けて置ける）。
  const [formKey, setFormKey] = useState(0)
  const [note, setNote] = useState('')

  return (
    <section className="vocab-sec" id="vocab-question" aria-labelledby="vocab-question-h">
      <h2 className="vocab-sec-h" id="vocab-question-h">
        {t('vocab:sections.question')}
      </h2>
      <p className="vocab-sec-sub">{t('vocab:question.hint')}</p>
      <label className="vocab-field">
        <span>{t('vocab:question.titleLabel')}</span>
        <input
          type="text"
          value={question}
          placeholder={t('vocab:question.titlePlaceholder')}
          onChange={(e) => setQuestion(e.target.value)}
        />
      </label>
      {question.trim() === '' ? (
        <p className="ds-empty-note">{t('vocab:question.empty')}</p>
      ) : (
        <>
          <div className="vocab-sec-sub">
            <strong>{t('vocab:question.place')}</strong>
          </div>
          <VocabTermForm
            key={formKey}
            question={question}
            onMinted={(term) => {
              setNote(t('vocab:question.placedNote', { label: term.label }))
              setFormKey((k) => k + 1)
              onChanged?.()
            }}
          />
        </>
      )}
      {note && <p className="lifecycle-ok">{note}</p>}
    </section>
  )
}
