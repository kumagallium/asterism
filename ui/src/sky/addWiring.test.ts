// データを置くも作業場で（契約 pr8c）の配線（ソース文字列）と、画面に出さない言葉の検査。
import { describe, expect, it } from 'vitest'
import skyEn from '../i18n/locales/en/sky.json'
import skyJa from '../i18n/locales/ja/sky.json'
import appSource from '../App.tsx?raw'
import consultSource from '../consult/ConsultDrawer.tsx?raw'
import datasetChatSource from '../kantan/DatasetChat.tsx?raw'
import wizardSource from '../kantan/KantanWizard.tsx?raw'
import addSource from './AddConversation.tsx?raw'
import skyViewSource from './SkyView.tsx?raw'

const strings = (o: unknown): string[] =>
  typeof o === 'string' ? [o] : o && typeof o === 'object' ? Object.values(o).flatMap(strings) : []

describe('取り付け: 右の領域は星図かかんたん', () => {
  it('ホームの route.add が SkyView の adding になり、右の領域に WorkbenchTier を描く（モーダルにしない）', () => {
    expect(appSource).toMatch(/adding=\{!!route\.add\}/)
    expect(appSource).toMatch(/addView=\{\s*route\.add \? \(\s*(?:\/\/[^\n]*\n\s*)*<WorkbenchTier/)
    expect(appSource).toMatch(/onCloseAdd=\{\(\) => navigate\(\{ tab: 'home', deep: route\.deep \}\)\}/)
    // gallery タブの add 描画は無くなった
    expect(appSource).not.toMatch(/tab === 'gallery' && route\.add/)
    expect(skyViewSource).not.toMatch(/createPortal|role="dialog"|aria-modal/)
  })

  it('データを置く入口はどこもホームの add へ。データの設計の見直しも同じ', () => {
    expect(appSource).not.toMatch(/navigate\(\{ tab: 'gallery', add: true \}\)/)
    expect(appSource).toMatch(/navigate\(\{ tab: 'home', add: true, placeDatasetId: target\.datasetId \}\)/)
  })

  it('上の行（答えられる問い）は星図のときだけ。星図の状態は隠すだけで mount したまま', () => {
    expect(skyViewSource).toMatch(/\{!adding && \(\s*<div className="sky-facts">/)
    expect(skyViewSource).toMatch(/<div className="sky-sky" hidden=\{adding\}>/)
  })

  it('星図に戻るとき星図を取り直し、生まれた輪は新しい星図が届くまで渡さない', () => {
    expect(skyViewSource).toMatch(/newIris=\{stale \? \[\] : props\.newIris\}/)
    expect(skyViewSource).toMatch(/if \(!adding\) setStale\(true\)/)
  })

  it('かんたんの段の描画と論理は触らない: ウィザードに足したのは段の押し出しだけ', () => {
    expect(wizardSource).toMatch(/setHostedStage\(recipePos, step === 9\)/)
    expect(wizardSource).toMatch(/useEffect\(\(\) => \(\) => clearHostedStage\(\), \[\]\)/)
    // 押し出すだけ: 読み戻さない
    expect(wizardSource).not.toMatch(/useHostedState|getHostedState/)
  })
})

describe('左の会話は 1 つ: 段で送り先が決まる', () => {
  it('③の会話（DatasetChat）は左の置き場へ portal し、置き場が無ければ従来どおり', () => {
    expect(datasetChatSource).toMatch(/createPortal\(chat, slot\)/)
    expect(datasetChatSource).toMatch(/useContext\(ChatSlotContext\)/)
    expect(datasetChatSource).toMatch(/screen="dataset"/)
    expect(datasetChatSource).toMatch(/embedded=\{!!slot\}/)
  })

  it('設計の相談（ConsultDrawer）の中身を左の欄へ。埋め込みでは浮かぶボタンもドロワーも body の class も使わない', () => {
    expect(addSource).toMatch(/<ConsultDrawer embedded/)
    expect(consultSource).toMatch(/if \(embedded\) \{\s*return \(\s*<section className="consult-embedded"/)
    expect(consultSource).toMatch(/if \(embedded\) return\s*document\.body\.classList\.toggle/)
  })

  it('左の欄は右下の「AI に相談」を隠す。送り先は dataDest（段）で決める。「いま: …」を出す', () => {
    expect(addSource).toMatch(/registerWorkspaceChat\(\)/)
    expect(addSource).toMatch(/dataDest\(h, pick\)/)
    expect(addSource).toMatch(/data-testid="now-line"/)
    // 両方 mount したまま、見せるほうだけ切り替える
    expect(addSource).toMatch(/hidden=\{dest !== 'bind'\}/)
    expect(addSource).toMatch(/hidden=\{dest !== 'consult'\}/)
  })

  it('AI 未設定: 左の欄は閉じて始める（人が開けば開いたまま）', () => {
    expect(skyViewSource).toMatch(/const addOpen = chatOpen && \(isReady \|\| addAsked\)/)
  })
})

describe('文言', () => {
  it('ja・en とも「いま:」の行・段ごとの 1 行・閉じるを持つ。点線・専門語は出さない', () => {
    for (const d of [skyJa, skyEn]) {
      expect(Object.keys(d.now.hint).sort()).toEqual(['bind', 'done', 's1', 's2', 's3', 's4', 's5', 's6', 's7'])
      expect(d.now.data).toContain('{{hint}}')
      expect(d.add.close).toBeTruthy()
    }
    const text = [...strings(skyJa.now), ...strings(skyJa.add)].join('\n')
    for (const bad of ['RML', 'IRI', 'SPARQL', 'モーダル', '点線']) expect(text).not.toContain(bad)
  })
})
