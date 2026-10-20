import { AskIcon, DataIcon, HomeIcon, LayersIcon, TermsIcon } from './icons'

/** 左ナビの項目 1 つ。`id` は App.tsx の hash ルータが使う `Tab` の一部
 *  （ここに出す項目だけの部分集合）。 */
export interface NavItem {
  id: 'home' | 'vocab' | 'gallery' | 'cards' | 'ask'
  icon: typeof HomeIcon
}

/**
 * 左ナビは 1 本・5 つ・順序固定・どの画面でも同じ（UI 再設計 PR 1）。純データ。
 * 見出し「作る」「使う」は廃止した——順序そのものが「問い→データ→見る→質問する」
 * の流れを表す。つながり（crosswalk）は「データ」の下のタブ、全体グラフ（network）は
 * ホームの「全体グラフを見る」、全体像（map）はデータセット画面から入る。
 * かんたんウィザード（`workbench`）も出さない（入口は星図の中）。「決めたこと」は
 * 用語のページと星の横から、開発者向け（SPARQL）は設定の「このサーバ」から入る。
 *
 * 下段（たたむ・設定）は App.tsx が直に描く。
 */
export const NAV_ITEMS: readonly NavItem[] = [
  { id: 'home', icon: HomeIcon },
  { id: 'vocab', icon: TermsIcon },
  { id: 'gallery', icon: DataIcon },
  { id: 'cards', icon: LayersIcon },
  { id: 'ask', icon: AskIcon },
]
