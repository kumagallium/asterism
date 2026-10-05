// 「ためす」の言葉のうち、その画面が実際にすることで変わるものを選ぶ。
//
// 同じ「ためす」に、動きの違う 2 つの運びが出る。
//
// | 運び | 問いが読むデータ | 先へ進むボタンがすること |
// |---|---|---|
// | 初回の流れ・見直しで下書きを作り直したあと | いま取り込んだ下書き | 「公開する」へ進む |
// | 見直しで、まだ作り直していない | 下書きがあれば下書き、無ければ公開した版 | 見直しを終えてデータセットのページへ戻る |
//
// 2 つめの運びにも 1 つめの言葉（「公開前の下書きに取り込めました」「問題なさ
// そう — 公開へ」）がそのまま出ていた。公開済みのデータセットを見直しで開いて
// 「ためす」へ出ると、読んでいるのは公開した版で、ボタンは公開の画面に進まない
// （実機 2026-10-05）。どちらのデータを読んだかは画面からは分からないので、
// サーバが答えに添える（`TrialQueries.read_from`）。

export interface TryWordingInput {
  /** 見直しで、まだ下書きを作り直していない。先へ進むボタンは見直しを終える。 */
  reviewOnly: boolean
  /** 問いを実行できなかった。 */
  trialFailed: boolean
  /** 問いは走ったが、1 件も返らなかった。 */
  trialEmpty: boolean
  /** サーバが問い合わせたデータ。まだ読めていない・古いサーバのときは無い。
   *  公開をやめたデータセット（'retracted'）は、どちらとも言わない側に倒す。 */
  readFrom?: 'draft' | 'published' | 'retracted' | null
}

/** どれも i18n のキー（`kantan:` つき）。 */
export interface TryWording {
  lead: string
  failed: string
  traceNote: string
  forward: string
}

export function tryWording({
  reviewOnly,
  trialFailed,
  trialEmpty,
  readFrom,
}: TryWordingInput): TryWording {
  if (!reviewOnly) {
    return {
      lead: 'kantan:s7.lead',
      failed: 'kantan:s7.failed',
      traceNote: 'kantan:s7.traceNote',
      forward: trialFailed
        ? 'kantan:s7.okNoTrial'
        : trialEmpty
          ? 'kantan:s7.okAnyway'
          : 'kantan:s7.ok',
    }
  }
  return {
    lead:
      readFrom === 'published'
        ? 'kantan:s7.leadReviewPublished'
        : readFrom === 'draft'
          ? 'kantan:s7.leadReviewDraft'
          : 'kantan:s7.leadReview',
    failed: 'kantan:redesign.trialFailed',
    // 公開した版の ID は、もうウェブ上の住所として開ける。「公開すると開ける
    // ようになります」は、これから公開するデータにだけ言う。
    traceNote: readFrom === 'published' ? 'kantan:s7.traceNotePublished' : 'kantan:s7.traceNote',
    forward: trialFailed
      ? 'kantan:redesign.finishNoTrial'
      : trialEmpty
        ? 'kantan:redesign.finishAnyway'
        : 'kantan:redesign.confirmNoChange',
  }
}
