// ホーム＝作業場（契約 pr8b）の配線（ソース文字列）と、画面に出さない言葉の検査。
// 会話・星図は React と canvas で動くので、ここでは「何がどこにつながっているか」を固定する。
import { describe, expect, it } from 'vitest'
import consultJa from '../i18n/locales/ja/consult.json'
import skyEn from '../i18n/locales/en/sky.json'
import skyJa from '../i18n/locales/ja/sky.json'
import themesJa from '../i18n/locales/ja/themes.json'
import appSource from '../App.tsx?raw'
import conversationSource from './HomeConversation.tsx?raw'
import hookSource from './useHomeWorkspace.ts?raw'
import skyViewSource from './SkyView.tsx?raw'
import themesViewSource from '../themes/ThemesView.tsx?raw'

const strings = (o: unknown): string[] =>
  typeof o === 'string' ? [o] : o && typeof o === 'object' ? Object.values(o).flatMap(strings) : []

describe('ホームの左の会話（常設）', () => {
  it('先頭に最初の一手のチップ 2 つ: ＋ データを置く（いまの入口へ）・？ 問いを書く（入力欄へフォーカス）', () => {
    expect(conversationSource).toMatch(/onClick=\{onAddData\}/)
    expect(conversationSource).toMatch(/ws\.writing \? ws\.cancelWriting\(\) : ws\.beginWriting\(\)/)
    expect(conversationSource).toMatch(/data-testid="chip-data"/)
    expect(conversationSource).toMatch(/data-testid="chip-question"/)
    expect(skyJa.chips).toEqual({ data: '＋ データを置く', question: '？ 問いを書く' })
    expect(skyJa.chat.writePlaceholder).toBe('知りたいことを 1 行で（例: 熱伝導率を下げる要素になり得るのは？）')
  })

  it('右下の「AI に相談」はホームでは出さない（会話が 2 つにならない）', () => {
    expect(conversationSource).toMatch(/registerWorkspaceChat\(\)/)
  })

  it('会話の先頭に札（pinned）・下に「N 件を置く」（bar）・AI 未設定は入力欄を閉じて書き込み欄', () => {
    expect(conversationSource).toMatch(/pinned=\{allPinned\}/)
    expect(conversationSource).toMatch(/bar=\{draft \? <PlaceBar/)
    expect(conversationSource).toMatch(/!isReady/)
    expect(conversationSource).toMatch(/<WriteForm/)
  })

  it('上の行の入口 2 つのピルは無い。星図の下の 1 行の帯・たためる会話・問いを書く合図を持つ', () => {
    expect(skyViewSource).not.toMatch(/sky-entries/)
    expect(skyViewSource).not.toMatch(/onWriteQuestion/)
    expect(skyViewSource).toMatch(/data-testid="questions-band"/)
    expect(skyViewSource).toMatch(/useSheet\(\)/)
    expect(skyViewSource).toMatch(/onWriteConsumed\(\)/)
    expect(skyViewSource).toMatch(/ws\.resume\(write\.resume\)/)
  })
})

describe('問いを書く → 図に入れる → N 件を置く（api は変えない）', () => {
  it('下書き・AI・相談・適用・置くは既存の api だけ', () => {
    for (const call of ['createDraft(', 'saveDraft(', 'draftWithAi(', "proposeGraph('theme'", 'applyGraph(', 'dismissGraph(', 'commitTheme(', "proposeOnScreen('home'", 'applyOnScreen(']) {
      expect(hookSource).toContain(call)
    }
  })

  it('置くのは本物だけ（提案の薄い行は送らない）・置けたら会話を新しくして星図を育てる', () => {
    expect(hookSource).toMatch(/buildCommitBody\(realDraft\(draftRef\.current \?\? saved, pendingRef\.current\), lang\)/)
    expect(hookSource).toMatch(/endSession\(\)\n\s+setItems\(\[\{ kind: 'note'/)
    expect(hookSource).toMatch(/o\.onPlaced\(result\)/)
    expect(skyViewSource).toMatch(/onPlaced: \(result\) => grow\(result\.minted\.map/)
  })

  it('テーマ名は AI の theme_label・無ければ最初の問いの先頭 20 字（人が付けた名前だけを AI に渡す）', () => {
    expect(hookSource).toMatch(/draftWithAi\(base\.questions, label\.trim\(\)/)
    expect(hookSource).toMatch(/defaultLabel\(questions\)/)
  })

  it('提案の道は 2 つ（下書きがあれば theme・無ければ星図の home）を 1 つの会話で持つ', () => {
    expect(hookSource).toMatch(/if \(draftRef\.current\) return sendTheme\(text\)/)
    expect(hookSource).toMatch(/if \(writing\) return startFromText\(text\)/)
    expect(hookSource).toMatch(/return sendHome\(text\)/)
    expect(hookSource).toMatch(/item\.screen === 'home'/)
  })
})

describe('②のモーダルは廃止・知りたいことは一覧だけ', () => {
  it('ThemeWizard・ThemeWorkspace・GraphCanvas はもう無い', () => {
    const files = Object.keys(import.meta.glob('../themes/*.tsx'))
    for (const name of ['ThemeWizard', 'ThemeWorkspace', 'GraphCanvas']) {
      expect(files.some((f) => f.includes(name))).toBe(false)
    }
    expect(appSource).not.toMatch(/ThemeWizard|ThemeWorkspace/)
    expect(themesViewSource).not.toMatch(/ThemeWizard|thm-overlay|thm-panel/)
  })

  it('一覧の「新しいテーマ・問いを足す・続ける」はホームの会話へ遷移する', () => {
    expect(themesViewSource).toMatch(/onClick=\{\(\) => write\(\)\}/)
    expect(themesViewSource).toMatch(/write\(\{ extend: \{ id: th\.id, label: th\.label \} \}\)/)
    expect(themesViewSource).toMatch(/write\(\{ resume: d\.id \}\)/)
    expect(appSource).toMatch(/onWrite=\{\(w\) => navigate\(\{ tab: 'home', write: w \}\)\}/)
  })
})

describe('画面に出さない言葉', () => {
  it('「下書きの札」「薄い」「点線」は sky・themes・consult の辞書に無い（印は「提案」）', () => {
    const all = [...strings(skyJa), ...strings(themesJa), ...strings(consultJa)]
    for (const word of ['下書きの札', '薄い', '点線']) {
      expect(all.filter((s) => s.includes(word))).toEqual([])
    }
    expect(skyJa.bands.draft).toBe('提案')
    expect(skyEn.bands.draft).toBe('proposal')
  })

  it('提案の返事は動作の言葉（図に入れる／入れない／1 件ずつ選ぶ／N 件を置く）。「そう」は残さない', () => {
    expect(themesJa.chat.yes).toBe('図に入れる（{{n}} 件）')
    expect(themesJa.chat.no).toBe('入れない')
    expect(themesJa.chat.per).toBe('1 件ずつ選ぶ')
    expect(themesJa.review.confirm).toBe('{{n}} 件を置く')
    expect(strings(themesJa.chat).filter((s) => /「そう」|そう$/.test(s))).toEqual([])
  })
})
