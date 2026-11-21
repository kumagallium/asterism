// 関係を足す入力欄の持ち主（R3c・契約 §1）。言えなかった関係の種類の行（`group`）からでも、空の入力欄（`group` なし）からでも同じ。
// 足せたら `onAdded(表示名)`。失敗は欄の下に 1 行（400 の検査・409 の「既にある関係」・上限・401/503）。
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { UnstatedGroup } from './knowledgeRelationsApi'
import { addFailureOf, addRelation } from './relationAddApi'
import { buildAdd, draftFor, exampleNotes, problemsOf, type RelationFormValues } from './relationFormModel'
import { RelationForm } from './RelationForm'

export function AddRelation({ group, lang, onAdded, onCancel }: { group: UnstatedGroup | null; lang: 'ja' | 'en'; onAdded: (label: string) => void; onCancel: () => void }) {
  const { t } = useTranslation('graphPlace')
  const [values, setValues] = useState<RelationFormValues>(() => draftFor(group))
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const submit = async () => {
    if (problemsOf(values).length > 0) return
    setPending(true)
    setError(null)
    try {
      await addRelation(buildAdd(values, lang, group?.key))
      onAdded(values.label.trim())
    } catch (e) {
      const f = addFailureOf(e)
      setError(f.kind === 'unavailable' ? t('added.unavailable') : f.message)
    } finally {
      setPending(false)
    }
  }
  return (
    <RelationForm
      testPrefix="gp-add"
      values={values}
      onChange={setValues}
      examples={exampleNotes(group)}
      pending={pending}
      error={error}
      submitLabel={t('act.addDo')}
      onSubmit={() => void submit()}
      onCancel={onCancel}
    />
  )
}
