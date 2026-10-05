// 見直しの「ためす」に出す「公開済みの項目名」の知らせ — 出すものを決める。
//
// 項目の意味を直して保存すると、書き換わるのは保存済みの設計だけ。公開側の名前
// （公開済みの ID を開いたページに出る名前）は、公開のときにしか変わらない。
// 意味だけを直した見直しは下書きを作らないので、その公開が来ない — 名前だけを
// 公開側へ出す入口を「ためす」に置く（ADR ontology-canonical-lifecycle.md §3.2）。
import type { PublishedNames, PublishedNameChange } from '../api'

export interface NamesToPublishInput {
  /** いま開いているデータセット。別のデータセットの答えは出さない。 */
  datasetId: string | null
  /** 見直しで、まだ下書きを作り直していない（tryWording と同じ条件）。 */
  reviewOnly: boolean
  /** 「ためす」の問いが読んだデータ（`TrialQueries.read_from`）。 */
  readFrom?: 'draft' | 'published' | 'retracted' | null
  /** サーバが見くらべた結果。まだ読めていない・読めなかったときは null。 */
  names: PublishedNames | null
}

/** 公開側にまだ出ていない名前。出すものが無い・出す場面ではないときは空。
 *
 *  出すのは「公開した版を読んでいる、作り直していない見直し」だけ:
 *  下書きがあれば名前はその公開で一緒に出るし、初回の流れにはまだ公開した版が無い。 */
export function namesToPublish({
  datasetId,
  reviewOnly,
  readFrom,
  names,
}: NamesToPublishInput): PublishedNameChange[] {
  if (!reviewOnly || readFrom !== 'published' || !names?.available) return []
  // 見直しを終えて別のデータセットを開いたあとに、前の問い合わせの答えが届くことが
  // ある。押すと書き換わるのはいま開いているほうなので、他人の一覧は見せない。
  if (!datasetId || names.dataset_id !== datasetId) return []
  return names.changes
}
