// ⑤⑥⑦ の配線（ソース文字列を見る）。書き方を変えると壊れやすいが、守りたい線は文字列で
// 見分けられる: 当てはめは人が受けたときだけ送る・機械は書かない・⑥の問いは下書きに保存・
// ⑦の件数は保管庫から読む。
import { describe, expect, it } from 'vitest'
import gateSource from '../SkeletonGate.tsx?raw'
import apiSource from '../api.ts?raw'
import vocabApiSource from '../vocabApi.ts?raw'
import source from './KantanWizard.tsx?raw'
import fitSource from './FitSuggestion.tsx?raw'
import kindFitSource from './KindFitSuggestion.tsx?raw'
import noticeSource from './PublishedNamesNotice.tsx?raw'
import builderSource from './QuestionBuilder.tsx?raw'

/** `function name(` から次の同じ深さの関数までの本体（ソース文字列のテスト用）。 */
function bodyOf(src: string, name: string): string {
  const at = src.search(new RegExp(`function ${name}\\(`))
  expect(at, `${name} が見つからない`).toBeGreaterThanOrEqual(0)
  const rest = src.slice(at + 1)
  const next = rest.search(/\n {2}(?:async )?function \w+\(/)
  return next < 0 ? rest : rest.slice(0, next)
}

describe('③ 項目の当てはめ提案', () => {
  it('見直し（reviewOnly）では受ける・外すを出さず、1 行の案内にする', () => {
    expect(source).toMatch(/<FitSuggestion[\s\S]*?reviewOnly=\{reviewOnly\}/)
    expect(fitSource).toMatch(/reviewOnly \? null :/)
    expect(fitSource).toMatch(/kantan:meanings\.fit\.reviewNote/)
  })

  it('③は項目（property）の候補だけを出す', () => {
    expect(fitSource).toMatch(/itemFitCandidates\(list\)/)
  })
})

describe('⑤ 種類の当てはめ提案', () => {
  it('upper は人が受けた・外したとき（upperTouched）だけ materialize に送る', () => {
    expect(source).toMatch(/\(upperTouched \|\| \(itemFits\?\.length \?\? 0\) > 0\) && skeleton/)
    expect(source).toMatch(/materializeSchema\([\s\S]*?settled,\s*upper,\s*\)/)
  })

  it('upperTouched を立てるのは、当てはめを受ける・外す操作だけ（種類 1・項目 3）', () => {
    const touches = source.match(/setUpperTouched\(true\)/g) ?? []
    expect(touches).toHaveLength(4)
    expect(bodyOf(source, 'onKindFit')).toMatch(/setUpperTouched\(true\)/)
    expect(bodyOf(source, 'setItemFit')).toMatch(/setUpperTouched\(true\)/)
    expect(bodyOf(source, 'editMeaningLabel')).toMatch(/setUpperTouched\(true\)/)
    expect(bodyOf(source, 'toggleColumnKept')).toMatch(/setUpperTouched\(true\)/)
  })

  it('項目の当てはめ（③の fit）は骨格ができたあと materialize の upper に重ねる', () => {
    expect(source).toMatch(/itemFitsToUpper\(settledMeanings, excludedColumns, skeleton\)/)
    expect(source).toMatch(/picksToUpper\(fits\.picks, skeleton, fits\.other, itemFits\)/)
    // 触っていなくても、受けた当てはめがあれば送る（スナップショットから戻った後でも落とさない）
    expect(source).toMatch(/upperTouched \|\| \(itemFits\?\.length \?\? 0\) > 0/)
  })

  it('③の fit は setItemFit 経由で受ける・外す（upper の印を立てる）', () => {
    expect(source).toMatch(/setItemFit\(row\.source, row\.column, null\)/)
    expect(source).toMatch(/setItemFit\(row\.source, row\.column, \{/)
  })

  it('SkeletonGate の提案は plain（かんたん層）で、受ける口があるときだけ出る', () => {
    expect(gateSource).toMatch(/plain && onKindFit && \(m\.subject\.classes \?\? \[\]\)\.length > 0/)
  })

  it('materialize の本体は upper を渡されたときだけ入れる（省略 = 既存を保つ）', () => {
    expect(apiSource).toMatch(/if \(upper\) body\.upper = upper/)
  })
})

describe('⑥ 自分の問い', () => {
  it('足す・消すは questions.json の置き換え保存（saveDatasetQuestions）', () => {
    expect(source).toMatch(/const saved = await saveDatasetQuestions\(datasetId, next\)/)
    expect(source).toMatch(/setOwnQuestions\(saved\)/)
    expect(source).toMatch(/<QuestionBuilder[\s\S]*?onChange=\{saveOwnQuestions\}/)
  })

  it('下書きは「ためす」を開くたびに読み戻す', () => {
    expect(source).toMatch(/fetchDatasetQuestions\(datasetId\)\s*\.then\(\(questions\) => \{\s*if \(current\(\)\) setOwnQuestions\(questions\)/)
  })
})

describe('⑦ 公開ダイアログ', () => {
  it('件数は保管庫の upper.json・questions.json から読み、0 件なら行を出さない', () => {
    expect(source).toMatch(/fetchDatasetUpper\(datasetId\)\.catch/)
    expect(source).toMatch(/publishWriteCounts\(pendingUpper, publishQuestions\)/)
    expect(source).toMatch(/\{writeCounts && \(/)
  })
})

describe('見直し経路の ☑（設計後の意味の見直し）', () => {
  it('saveMeaningsAndReturn は意味の保存のあと、☑ を読み戻して変わっていれば PUT する', () => {
    expect(bodyOf(source, 'saveMeaningsAndReturn')).toMatch(/await saveLinkTicks\(datasetId\)/)
    const body = bodyOf(source, 'saveLinkTicks')
    expect(body).toMatch(/await fetchDatasetHandles\(datasetId\)/)
    expect(body).toMatch(/if \(handlesChanged\(stored, next\)\) await saveDatasetHandles\(datasetId, next\)/)
  })

  it('読み戻せていない見直しで人も触っていないなら、空の state で既存の ☑ を消さない', () => {
    const body = bodyOf(source, 'saveLinkTicks')
    expect(body).toMatch(/linkHandlesHydratedFor\.current !== datasetId/)
    expect(body).toMatch(/!linkTouched/)
    expect(body).toMatch(/if \(unhydrated\) return/)
  })

  it('保存する ☑ は judgments().ticked から作る（取り込まない列は入らない）', () => {
    expect(bodyOf(source, 'saveLinkTicks')).toMatch(
      /tickedHandles\(judgments\(judgmentState\(\)\)\.ticked, linkFitTerms\)/,
    )
  })

  it('PUT /handles を送る関数がある', () => {
    expect(apiSource).toMatch(/export async function saveDatasetHandles/)
    expect(apiSource).toMatch(/\/handles`, \{\s*method: 'PUT'/)
  })
})

describe('名前だけの公開の入口', () => {
  it('見直しで未適用の線か問いがあれば、名前の差が無くても PublishedNamesNotice に件数を出す', () => {
    expect(source).toMatch(/publishWriteCounts\(pendingUpper, ownQuestions\)/)
    expect(source).toMatch(/writes=\{reviewWrites\}/)
    expect(source).toMatch(/reviewOnly && !trialLoading && trial\?\.read_from === 'published'/)
    expect(noticeSource).toMatch(/changes\.length === 0 && !writes/)
  })

  it('既存の publish-names を呼び、応答の upper_questions を画面に出す', () => {
    expect(bodyOf(source, 'runPublishNames')).toMatch(/publishDatasetNames\(datasetId\)/)
    expect(bodyOf(source, 'runPublishNames')).toMatch(/setPublishReport\(res\.upper_questions/)
    expect(noticeSource).toMatch(/<UpperQuestionsResult report=\{report\}/)
  })

  it('「ためす」を開くたびに未消費の線を読み戻す', () => {
    expect(bodyOf(source, 'loadS7')).toMatch(/fetchDatasetUpper\(datasetId\)/)
  })
})

describe('リセット漏れ（resetWizardToStart）', () => {
  it('骨格の ☑・番号・当てはめ・公開の材料・問いを初期化する', () => {
    const body = bodyOf(source, 'resetWizardToStart')
    for (const call of [
      'setKindPicks({})',
      'setUpperTouched(false)',
      'setUpperBase(null)',
      'setLinkFitTerms({})',
      'setLinkChecked(new Set())',
      'setLinkKeyPick({})',
      'setLinkTouched(false)',
      'setPendingUpper([])',
      'setPublishQuestions([])',
      'setOwnQuestions([])',
    ]) {
      expect(body, call).toContain(call)
    }
  })
})

describe('非同期の取りこぼし', () => {
  it('QuestionBuilder の「ためしに聞く」は、応答時に選択が変わっていたら捨てる', () => {
    expect(builderSource).toMatch(/selKeyRef\.current = selectionKey\(sel\)/)
    expect(builderSource.match(/if \(selKeyRef\.current !== key\) return/g)).toHaveLength(2)
  })

  it('問いの読み込みは datasetId と最新の保存を確かめてから載せる', () => {
    const body = bodyOf(source, 'loadS7')
    expect(body).toMatch(/kzDatasetIdRef\.current === datasetId && questionsSeq\.current === seq/)
    expect(bodyOf(source, 'saveOwnQuestions')).toMatch(/questionsSeq\.current \+= 1/)
  })

  it('当てはめ提案は入力が変わった瞬間に候補を空にする（入力の鍵が合うときだけ出す）', () => {
    for (const src of [fitSource, kindFitSource]) {
      expect(src).toMatch(/found\.key === inputKey \? found\.list : \[\]/)
    }
  })
})

describe('⑦ の件数と応答', () => {
  it('promote の応答 upper_questions を完了画面に出す', () => {
    expect(source).toMatch(/setPublishReport\(res\.upper_questions \?\? null\)/)
    expect(source).toMatch(/<UpperQuestionsResult report=\{publishReport\}/)
  })
})

describe('「ことばへ写す」の重複', () => {
  it('写したあとは押せず、api が 200（既存）なら「すでに写してあります」を出す', () => {
    expect(builderSource).toMatch(/mapBusy === q\.id \|\| note\?\.kind === 'done'/)
    expect(builderSource).toMatch(/note\.existed \? 'kantan:s7\.own\.mapExisted'/)
    expect(vocabApiSource).toMatch(/existed: res\.status === 200/)
  })
})
