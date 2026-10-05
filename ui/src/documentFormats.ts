// 文書として取り込める形式の可否。Word・PDF はサーバ側の変換部品に依存するので、
// `/api/instance` の can_convert_* で「置く前に」分かるようにする。XML は変換が要らず常に可。

import { useEffect, useState } from 'react'
import type { TFunction } from 'i18next'
import { fetchInstanceInfo, type InstanceInfo } from './settings/instanceApi'

export type ConvertedFormat = 'docx' | 'pdf'
export interface DocumentFormats {
  docx: boolean
  pdf: boolean
  /** 案内の言い方（デスクトップ／サーバ）を分けるためだけに使う。可否の判定には使わない。 */
  desktop: boolean
}

/** 旧 api（フィールド無し）・取得失敗（null）は「変換できる」とみなす（サーバの 4xx が最後の砦）。 */
export function documentFormatsOf(info: InstanceInfo | null): DocumentFormats {
  return {
    docx: info?.can_convert_docx !== false,
    pdf: info?.can_convert_pdf !== false,
    desktop: info?.desktop === true,
  }
}

export function documentExts(f: DocumentFormats): string[] {
  return ['.xml', ...(f.docx ? ['.docx'] : []), ...(f.pdf ? ['.pdf'] : [])]
}

export function documentAccept(f: DocumentFormats): string {
  return documentExts(f).join(',')
}

export function availableFormats(f: DocumentFormats): ConvertedFormat[] {
  return [...(f.docx ? (['docx'] as const) : []), ...(f.pdf ? (['pdf'] as const) : [])]
}

export function unavailableFormats(f: DocumentFormats): ConvertedFormat[] {
  return [...(!f.docx ? (['docx'] as const) : []), ...(!f.pdf ? (['pdf'] as const) : [])]
}

/** 拡張子（大文字小文字無視）が .docx/.pdf で、かつ変換できないときその形式。 */
export function unavailableFormatOf(name: string, f: DocumentFormats): ConvertedFormat | null {
  const lower = name.toLowerCase()
  if (lower.endsWith('.docx') && !f.docx) return 'docx'
  if (lower.endsWith('.pdf') && !f.pdf) return 'pdf'
  return null
}

/** i18next の context 用。両方可→undefined／PDF だけ不可→'noPdf'／Word だけ不可→'noDocx'／両方不可→'none'。 */
export function formatsContext(f: DocumentFormats): 'noPdf' | 'noDocx' | 'none' | undefined {
  if (f.docx && f.pdf) return undefined
  if (f.docx) return 'noPdf'
  if (f.pdf) return 'noDocx'
  return 'none'
}

/** 取り込める Word/PDF を ' / ' で連結。 */
export function docsLabel(f: DocumentFormats): string {
  return [...(f.docx ? ['Word'] : []), ...(f.pdf ? ['PDF'] : [])].join(' / ')
}

/** XML も含めた一覧。 */
export function formatsLabel(f: DocumentFormats): string {
  return [...(f.docx ? ['Word'] : []), ...(f.pdf ? ['PDF'] : []), 'XML'].join(' / ')
}

/** 拡張子つきの一覧。 */
export function formatsWithExtLabel(f: DocumentFormats): string {
  return [...(f.docx ? ['Word .docx'] : []), ...(f.pdf ? ['PDF .pdf'] : []), 'XML .xml'].join(' / ')
}

/** 変換できない形式が 1 つでもあるときの案内文（無ければ空文字）。 */
export function unavailableNote(t: TFunction, f: DocumentFormats): string {
  if (f.docx && f.pdf) return ''
  const formats =
    !f.docx && !f.pdf ? t('document:unavailable.both') : !f.docx ? 'Word' : 'PDF'
  return t(f.desktop ? 'document:unavailable.noteDesktop' : 'document:unavailable.noteServer', {
    formats,
  })
}

/** 変換できない形式のファイルを置いたときの文。 */
export function unavailableDropMessage(
  t: TFunction,
  f: DocumentFormats,
  format: ConvertedFormat,
): string {
  return t(f.desktop ? 'document:unavailable.dropDesktop' : 'document:unavailable.dropServer', {
    format: t(format === 'docx' ? 'document:unavailable.docx' : 'document:unavailable.pdf'),
  })
}

/** 置かれたファイル群のうち最初の「変換できない形式」。無ければ null。 */
export function firstUnavailableIn(
  files: ArrayLike<{ name: string }>,
  f: DocumentFormats,
): ConvertedFormat | null {
  for (const file of Array.from(files)) {
    const u = unavailableFormatOf(file.name, f)
    if (u) return u
  }
  return null
}

// モジュールレベルでメモ化された fetchInstanceInfo を 1 回読む。初期値は「全部できる」。
export function useDocumentFormats(): DocumentFormats {
  const [formats, setFormats] = useState<DocumentFormats>(() => documentFormatsOf(null))
  useEffect(() => {
    let alive = true
    void fetchInstanceInfo().then((info) => {
      if (alive) setFormats(documentFormatsOf(info))
    })
    return () => {
      alive = false
    }
  }, [])
  return formats
}
