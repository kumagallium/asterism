import { useState } from 'react'
import type { DetailTab } from '../redesignTarget'
import type { RedesignTarget } from '../redesignTarget'
import { type GrowFocus, KantanWizard } from './KantanWizard'

// データを追加の入口。かんたんウィザードだけを出す（詳細モードは無い）。

export function WorkbenchTier({
  redesignTarget,
  onRedesignConsumed,
  onOpenDataset,
  onOpenAsk,
  onShortcutDone,
}: {
  redesignTarget?: RedesignTarget | null
  onRedesignConsumed?: () => void
  /** `focus` says which of S9's two grow exits was taken (add / replace), so
   *  the catalog can land on that control; receivers may ignore it. */
  onOpenDataset?: (id: string, tab?: DetailTab, focus?: GrowFocus) => void
  /** Opens the Ask view with a question prefilled (the kantan S9 chips). */
  onOpenAsk?: (question: string) => void
  /** S1 の「同じ形なら設計なしで追加」の帯（契約メモ contract_pr_f10.md
   *  §1.2-2）が「そのまま追加」で完了したときの着地先。渡さなければ帯は
   *  出ない（かんたんウィザードは他の入口——`#/workbench` の見直す等——でも
   *  この Tier をそのまま使うため、対応する完了経路が無い呼び出し元では帯を
   *  一切出さない）。 */
  onShortcutDone?: (target: { datasetId: string; classIri?: string }) => void
}) {
  const [, setKantanBusy] = useState(false)
  return (
    <div className="kz-tier">
      <KantanWizard
        onBusyChange={setKantanBusy}
        onOpenDataset={onOpenDataset}
        onOpenAsk={onOpenAsk}
        redesignTarget={redesignTarget}
        onRedesignConsumed={onRedesignConsumed}
        onShortcutDone={onShortcutDone}
      />
    </div>
  )
}
