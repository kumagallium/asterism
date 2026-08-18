import i18n from 'i18next'
import LanguageDetector from 'i18next-browser-languagedetector'
import { initReactI18next } from 'react-i18next'
import {
  bootstrapAppSettings,
  getServerSetting,
  isAppSettingsServerMode,
  putAppSetting,
} from '../appSettings'

// 日本語ファースト。既定 ja、ユーザーが切り替えたら localStorage に保存。
// ブラウザ言語では自動切替しない（友人含めユーザー層が日本語話者のため）。
export const SUPPORTED_LNGS = ['ja', 'en'] as const
export type Lng = (typeof SUPPORTED_LNGS)[number]
export const DEFAULT_LNG: Lng = 'ja'
export const LANG_STORAGE_KEY = 'asterism.lang'

// locales/<lng>/<namespace>.json を全部自動ロードして resources を組む。
// 画面ごとに自分の namespace ファイルだけ足せばよく、この index は触らない。
const modules = import.meta.glob('./locales/*/*.json', { eager: true }) as Record<
  string,
  { default: Record<string, unknown> }
>

const resources: Record<string, Record<string, Record<string, unknown>>> = {}
for (const path in modules) {
  const m = path.match(/\.\/locales\/([^/]+)\/([^/]+)\.json$/)
  if (!m) continue
  const [, lng, ns] = m
  resources[lng] ??= {}
  resources[lng][ns] = modules[path].default
}

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: DEFAULT_LNG,
    supportedLngs: SUPPORTED_LNGS as unknown as string[],
    defaultNS: 'common',
    interpolation: { escapeValue: false },
    detection: {
      // ブラウザ言語は見ない＝初回は必ず日本語。以降は localStorage を尊重。
      order: ['localStorage'],
      lookupLocalStorage: LANG_STORAGE_KEY,
      // localStorage への書き込みはこの後も残す（ADR app-data-on-disk.md:
      // singleUser モードでもサーバの値が届くまでの起動時キャッシュとして使う）。
      caches: ['localStorage'],
    },
  })

// singleUser モード（ADR app-data-on-disk.md D1）ではサーバの settings.json の
// `lang` が正。起動直後は localStorage の値でそのまま描画し（ちらつき防止）、
// サーバの申告が届いてから食い違っていれば切り替える。以降のユーザーによる
// 切り替えはここで拾ってサーバへ書き戻す（localStorage への書き込みは detector
// がそのまま担う）。
//
// .ts から i18n シングルトンを使うときの罠: 参照はここ（モジュール内の関数）
// でだけ行う — この index.ts はその定義元そのものなので安全。
if (typeof window !== 'undefined') {
  void bootstrapAppSettings().then(() => {
    if (!isAppSettingsServerMode()) return
    const remote = getServerSetting<string>('lang')
    if (remote && remote !== i18n.language && (SUPPORTED_LNGS as readonly string[]).includes(remote)) {
      void i18n.changeLanguage(remote)
    }
  })
  i18n.on('languageChanged', (lng) => {
    if (isAppSettingsServerMode()) putAppSetting('lang', lng)
  })
}

export default i18n
