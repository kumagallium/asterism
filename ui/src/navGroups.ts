import {
  ActivityIcon,
  AskIcon,
  ConnectIcon,
  DataIcon,
  HomeIcon,
  LayersIcon,
  TermsIcon,
} from './icons'

/** 左ナビの項目 1 つ（契約メモ contract_pr_f7.md §1）。`id` は App.tsx の hash
 *  ルータが使う `Tab` の一部（ここに出す項目だけの部分集合）。 */
export interface NavItem {
  id: 'home' | 'gallery' | 'crosswalk' | 'vocab' | 'jobs' | 'cards' | 'ask'
  icon: typeof HomeIcon
}

/** 左ナビの見出し 1 つ分（契約メモ §1: 「作る」「使う」）。`key` から
 *  i18n キー `nav.group_${key}` を組み立てる。 */
export interface NavGroup {
  key: 'build' | 'use'
  items: NavItem[]
}

/**
 * 左ナビは 1 本・どの画面でも同じ（契約メモ contract_pr_f7.md §1）。純データ
 * ——「1 回 Read すれば済む」判断（何を作るか・つなぐか）と「使う」（見る・問う）
 * を見出しで分ける、というユーザーの指摘をそのまま形にする。
 *
 * かんたんウィザード（`workbench`）はここに出さない（契約メモ §1 注記どおり）
 * ——データ（gallery）・ワークスペース（cards）から入る導線のまま。
 */
export const NAV_GROUPS: readonly NavGroup[] = [
  {
    key: 'build',
    items: [
      { id: 'home', icon: HomeIcon },
      { id: 'gallery', icon: DataIcon },
      { id: 'crosswalk', icon: ConnectIcon },
      { id: 'vocab', icon: TermsIcon },
      { id: 'jobs', icon: ActivityIcon },
    ],
  },
  {
    key: 'use',
    items: [
      { id: 'cards', icon: LayersIcon },
      { id: 'ask', icon: AskIcon },
    ],
  },
]
