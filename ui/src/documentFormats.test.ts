import i18next, { type TFunction } from 'i18next'
import { beforeAll, describe, expect, it } from 'vitest'
import type { InstanceInfo } from './settings/instanceApi'
import {
  docsLabel,
  documentAccept,
  documentExts,
  documentFormatsOf,
  firstUnavailableIn,
  formatsContext,
  formatsLabel,
  formatsWithExtLabel,
  unavailableDropMessage,
  unavailableFormatOf,
  pdfInstallable,
  unavailableNote,
  unavailableNotes,
  type DocumentFormats,
} from './documentFormats'
import { isManageable, progressPercent, sizeGb } from './pdfRuntime'
import type { PdfRuntimeState, PdfRuntimeStatus } from './settings/pdfRuntimeApi'
import jaSettings from './i18n/locales/ja/settings.json'
import jaDocument from './i18n/locales/ja/document.json'
import jaGallery from './i18n/locales/ja/gallery.json'
import jaKantan from './i18n/locales/ja/kantan.json'
import jaWorkbench from './i18n/locales/ja/workbench.json'

const base: InstanceInfo = { iri_base: 'x', iri_base_configured: true }
const both: DocumentFormats = { docx: true, pdf: true, desktop: false }
const noPdf: DocumentFormats = { docx: true, pdf: false, desktop: false }
const noDocx: DocumentFormats = { docx: false, pdf: true, desktop: false }
const none: DocumentFormats = { docx: false, pdf: false, desktop: true }

describe('documentFormatsOf', () => {
  it('null は全部できる', () => {
    expect(documentFormatsOf(null)).toEqual({ docx: true, pdf: true, desktop: false })
  })
  it('フィールド無しは全部できる', () => {
    expect(documentFormatsOf(base)).toEqual(both)
  })
  it('false 混在', () => {
    expect(documentFormatsOf({ ...base, can_convert_docx: true, can_convert_pdf: false })).toEqual(noPdf)
    expect(documentFormatsOf({ ...base, can_convert_docx: false, can_convert_pdf: true })).toEqual(noDocx)
  })
  it('desktop', () => {
    expect(documentFormatsOf({ ...base, desktop: true, can_convert_docx: false, can_convert_pdf: false })).toEqual(none)
  })
})

describe('ext / accept', () => {
  it('組み立て', () => {
    expect(documentExts(both)).toEqual(['.xml', '.docx', '.pdf'])
    expect(documentExts(noPdf)).toEqual(['.xml', '.docx'])
    expect(documentExts(none)).toEqual(['.xml'])
    expect(documentAccept(noDocx)).toBe('.xml,.pdf')
  })
})

describe('unavailableFormatOf', () => {
  it('大文字小文字を無視する', () => {
    expect(unavailableFormatOf('REPORT.DOCX', none)).toBe('docx')
    expect(unavailableFormatOf('a.pdf', noPdf)).toBe('pdf')
  })
  it('xml と変換できる形式は null', () => {
    expect(unavailableFormatOf('a.xml', none)).toBeNull()
    expect(unavailableFormatOf('a.docx', noPdf)).toBeNull()
  })
})

describe('formatsContext', () => {
  it('4 通り', () => {
    expect(formatsContext(both)).toBeUndefined()
    expect(formatsContext(noPdf)).toBe('noPdf')
    expect(formatsContext(noDocx)).toBe('noDocx')
    expect(formatsContext(none)).toBe('none')
  })
})

describe('labels', () => {
  it('docsLabel', () => {
    expect(docsLabel(both)).toBe('Word / PDF')
    expect(docsLabel(noPdf)).toBe('Word')
    expect(docsLabel(noDocx)).toBe('PDF')
    expect(docsLabel(none)).toBe('')
  })
  it('formatsLabel', () => {
    expect(formatsLabel(both)).toBe('Word / PDF / XML')
    expect(formatsLabel(noPdf)).toBe('Word / XML')
    expect(formatsLabel(noDocx)).toBe('PDF / XML')
    expect(formatsLabel(none)).toBe('XML')
  })
  it('formatsWithExtLabel', () => {
    expect(formatsWithExtLabel(both)).toBe('Word .docx / PDF .pdf / XML .xml')
    expect(formatsWithExtLabel(none)).toBe('XML .xml')
  })
})

// 辞書と組み合わせた文言。画面の呼び出しと同じ引数で引き、できあがりの文を確かめる。
describe('文言（ja の辞書と組み合わせて）', () => {
  let t: TFunction
  beforeAll(async () => {
    const i18n = i18next.createInstance()
    await i18n.init({
      lng: 'ja',
      resources: {
        ja: { document: jaDocument, settings: jaSettings, gallery: jaGallery, kantan: jaKantan, workbench: jaWorkbench },
      },
      interpolation: { escapeValue: false },
    })
    t = i18n.t
  })

  // 画面が出し分ける文を、呼び出し側と同じ形で全部引く。
  function render(f: DocumentFormats): Record<string, string> {
    const ctx = formatsContext(f)
    const noneCtx = ctx === 'none' ? 'none' : undefined
    return {
      dropFormats: t('kantan:s1.dropFormats', { docs: docsLabel(f), context: noneCtx }),
      unsupported: t('kantan:s1.unsupported', { context: ctx }),
      mixed: t('kantan:s1.mixed', { docs: docsLabel(f), context: ctx }),
      documentNote: t('kantan:s1.documentNote', { docs: docsLabel(f), context: noneCtx }),
      wrongKind: t('kantan:s5.stop.wrongKind.document', { formats: formatsLabel(f) }),
      intro: t('document:intro', { formats: formatsWithExtLabel(f) }),
      introPlain:
        ctx === 'none'
          ? t('document:introPlain_none')
          : t('document:introPlain', { docs: docsLabel(f) }),
      pickFile: t('document:pickFile', { formats: formatsLabel(f) }),
      convertHint: t('document:convertHint', { context: ctx }),
      convertHintPlain: t('document:convertHintPlain', { context: ctx }),
      appendNote: t('gallery:docAppend.note', { formats: formatsLabel(f) }),
      appendPick: t('gallery:docAppend.pick', { formats: formatsLabel(f) }),
      sourceLabel: t('workbench:source.document', { formats: formatsLabel(f) }),
    }
  }

  it('両方できる環境では、出し分ける前の文言と同じ', () => {
    const r = render(both)
    expect(r.dropFormats).toBe('Excel・CSV・装置が出力したファイル（.txt / .dat）・Word / PDF')
    expect(r.unsupported).toBe(
      'このファイルの形式にはまだ対応していません。Excel・CSV・装置の出力ファイル・Word・PDF を置いてください。',
    )
    expect(r.mixed).toContain('・JSON・文書（Word / PDF）は、分けて置いてください。')
    expect(r.documentNote).toMatch(/^Word \/ PDF は、このまま追加できます（AI は使いません）。/)
    expect(r.wrongKind).toContain('文書のファイル（Word / PDF / XML）から作りました')
    expect(r.intro).toMatch(/（対応: Word \.docx \/ PDF \.pdf \/ XML \.xml）$/)
    expect(r.introPlain).toMatch(/^Word \/ PDF はそのまま取り込めます。/)
    expect(r.pickFile).toBe('文書を選択（Word / PDF / XML）')
    expect(r.convertHintPlain).toBe(
      'Word・PDF はそのまま置けます。PDF は読み取りに数分かかることがあります。',
    )
    expect(r.appendNote).toMatch(/^Word \/ PDF \/ XML の文書をもう 1 つ足すと、/)
    expect(r.appendPick).toBe('文書を選択（Word / PDF / XML）')
    expect(r.sourceLabel).toBe('文書（Word / PDF / XML）')
    expect(unavailableNote(t, both)).toBe('')
  })

  it('取り込めない形式は、文言から外れる', () => {
    const d = render(none)
    expect(d.dropFormats).toBe('Excel・CSV・装置が出力したファイル（.txt / .dat）')
    expect(d.pickFile).toBe('文書を選択（XML）')
    expect(d.introPlain).toMatch(/^論文の XML はそのまま取り込めます。/)
    const p = render(noPdf)
    expect(p.dropFormats).toBe('Excel・CSV・装置が出力したファイル（.txt / .dat）・Word')
    expect(p.pickFile).toBe('文書を選択（Word / XML）')
    expect(p.convertHintPlain).toBe('Word はそのまま置けます。')
    // Word も PDF も出てこないはずの文に、取り込めない形式の名前が残っていない
    for (const text of [d.dropFormats, d.unsupported, d.mixed, d.documentNote, d.pickFile]) {
      expect(text).not.toMatch(/Word|PDF/)
    }
    for (const text of [p.dropFormats, p.unsupported, p.mixed, p.documentNote, p.pickFile]) {
      expect(text).not.toContain('PDF')
    }
  })

  it('どの組み合わせでも、埋め残し（{{…}}）が出ない', () => {
    for (const f of [both, noPdf, noDocx, none]) {
      for (const [key, text] of Object.entries(render(f))) {
        expect(text, key).not.toContain('{{')
      }
    }
  })

  it('案内文は、言い方だけをデスクトップ／サーバで分ける', () => {
    expect(unavailableNote(t, none)).toBe('Word・PDF は、デスクトップ版ではまだ取り込めません。')
    expect(unavailableNote(t, noPdf)).toBe(
      'PDF は、このサーバでは取り込めません（読み取る部品が設定されていません）。',
    )
    expect(unavailableNote(t, { docx: false, pdf: true, desktop: true })).toBe(
      'Word は、デスクトップ版ではまだ取り込めません。',
    )
  })

  it('置いたときの文は、形式を名指しする', () => {
    expect(unavailableDropMessage(t, none, 'docx')).toBe(
      'Word（.docx）は、デスクトップ版ではまだ取り込めません。いまのところサーバ版が対象です。',
    )
    expect(unavailableDropMessage(t, noPdf, 'pdf')).toBe(
      'PDF（.pdf）は、このサーバでは取り込めません（読み取る部品が設定されていません）。',
    )
  })
})

describe('firstUnavailableIn', () => {
  it('置かれた中で最初の「変換できない形式」を返す', () => {
    const files = [{ name: 'a.xml' }, { name: 'b.PDF' }, { name: 'c.docx' }]
    expect(firstUnavailableIn(files, none)).toBe('pdf')
    expect(firstUnavailableIn(files, noDocx)).toBe('docx')
    expect(firstUnavailableIn(files, both)).toBeNull()
    expect(firstUnavailableIn([{ name: 'table.csv' }], none)).toBeNull()
  })
})

// ---- PDF の読み取り部品 ----
const rt = (state: PdfRuntimeState, extra: Partial<PdfRuntimeStatus> = {}): PdfRuntimeStatus => ({
  state,
  phase: null,
  bytes_done: 0,
  bytes_total: 0,
  error: null,
  log_path: null,
  ...extra,
})
const dNoPdf: DocumentFormats = { docx: true, pdf: false, desktop: true }
const dNone: DocumentFormats = { docx: false, pdf: false, desktop: true }

describe('manageable / 進み具合', () => {
  it('isManageable', () => {
    expect(isManageable(null)).toBe(false)
    expect(isManageable(rt('unsupported'))).toBe(false)
    expect(isManageable(rt('external'))).toBe(false)
    for (const s of ['absent', 'installing', 'starting', 'ready', 'failed'] as const) {
      expect(isManageable(rt(s))).toBe(true)
    }
  })
  it('progressPercent', () => {
    expect(progressPercent({ bytes_done: 0, bytes_total: 0 })).toBe(0)
    expect(progressPercent({ bytes_done: 0, bytes_total: 100 })).toBe(0)
    expect(progressPercent({ bytes_done: 50, bytes_total: 200 })).toBe(25)
    expect(progressPercent({ bytes_done: 100, bytes_total: 100 })).toBe(99)
    expect(progressPercent({ bytes_done: 300, bytes_total: 100 })).toBe(99)
  })
  it('sizeGb', () => {
    expect(sizeGb({ bytes_total: 1.74e9 })).toBe('1.7')
    expect(sizeGb({ bytes_total: 0 })).toBe('1.7')
  })
})

describe('pdfInstallable', () => {
  it('PDF 不可かつ absent・failed・installing のときだけ true', () => {
    expect(pdfInstallable(dNoPdf, rt('absent'))).toBe(true)
    expect(pdfInstallable(dNoPdf, rt('failed'))).toBe(true)
    expect(pdfInstallable(dNoPdf, rt('installing'))).toBe(true)
    expect(pdfInstallable(dNoPdf, rt('starting'))).toBe(false)
    expect(pdfInstallable(dNoPdf, rt('external'))).toBe(false)
    expect(pdfInstallable(dNoPdf, null)).toBe(false)
    expect(pdfInstallable(both, rt('absent'))).toBe(false)
  })
})

describe('PDF 部品まわりの文言', () => {
  let t: TFunction
  beforeAll(async () => {
    const i18n = i18next.createInstance()
    await i18n.init({
      lng: 'ja',
      resources: { ja: { document: jaDocument, settings: jaSettings } },
      interpolation: { escapeValue: false },
    })
    t = i18n.t
  })

  it('管理できない環境では、今の 1 行と同じ', () => {
    for (const p of [null, rt('unsupported'), rt('external')]) {
      expect(unavailableNotes(t, dNone, p)).toEqual([unavailableNote(t, dNone)])
      expect(unavailableNotes(t, dNoPdf, p)).toEqual([unavailableNote(t, dNoPdf)])
    }
  })
  it('両方できるなら空', () => {
    expect(unavailableNotes(t, both, null)).toEqual([])
    expect(unavailableNotes(t, both, rt('absent'))).toEqual([])
  })
  it('Word だけ不可なら、今のまま', () => {
    expect(unavailableNotes(t, { docx: false, pdf: true, desktop: true }, rt('absent'))).toEqual([
      'Word は、デスクトップ版ではまだ取り込めません。',
    ])
  })
  it('PDF 不可・管理できる環境', () => {
    expect(unavailableNotes(t, dNoPdf, rt('absent'))).toEqual([
      'PDF は、読み取る部品を入れると取り込めます。',
    ])
    expect(unavailableNotes(t, dNoPdf, rt('failed'))).toEqual([
      'PDF は、読み取る部品を入れると取り込めます。',
    ])
    expect(unavailableNotes(t, dNoPdf, rt('installing'))[0]).toMatch(/^PDF を読み取る部品を入れています/)
    expect(unavailableNotes(t, dNoPdf, rt('starting'))).toEqual(['PDF を読み取る準備をしています…'])
  })
  it('Word も不可なら 2 行（Word が先）', () => {
    expect(unavailableNotes(t, dNone, rt('absent'))).toEqual([
      'Word は、デスクトップ版ではまだ取り込めません。',
      'PDF は、読み取る部品を入れると取り込めます。',
    ])
  })
  it('置いたときの文', () => {
    expect(unavailableDropMessage(t, dNoPdf, 'pdf', rt('absent'))).toMatch(/設定の「PDF」タブ/)
    expect(unavailableDropMessage(t, dNoPdf, 'pdf', rt('failed'))).toMatch(/設定の「PDF」タブ/)
    expect(unavailableDropMessage(t, dNoPdf, 'pdf', rt('installing'))).toMatch(/入れている最中/)
    expect(unavailableDropMessage(t, dNoPdf, 'pdf', rt('starting'))).toMatch(/入れている最中/)
    // Word を置いたとき・管理できないときは今のまま
    expect(unavailableDropMessage(t, dNone, 'docx', rt('absent'))).toBe(unavailableDropMessage(t, dNone, 'docx'))
    expect(unavailableDropMessage(t, dNoPdf, 'pdf', rt('external'))).toBe(unavailableDropMessage(t, dNoPdf, 'pdf'))
  })
  it('設定のタブは、どの state・phase でも埋め残しが出ない', () => {
    const size = sizeGb(rt('absent', { bytes_total: 1.7e9 }))
    const texts = [
      t('settings:pdf.intro', { size }),
      t('settings:pdf.install', { size }),
      t('settings:pdf.installNote'),
      ...(['packages', 'models', 'starting'] as const).map((p) => t(`settings:pdf.phase.${p}`)),
      t('settings:pdf.progress', { pct: progressPercent({ bytes_done: 1, bytes_total: 3 }) }),
      t('settings:pdf.keepsGoing'),
      t('settings:pdf.cancel'),
      t('settings:pdf.starting'),
      t('settings:pdf.ready'),
      t('settings:pdf.readyNote', { size }),
      t('settings:pdf.remove'),
      t('settings:pdf.removeConfirm', { size }),
      t('settings:pdf.removeYes'),
      t('settings:pdf.removeNo'),
      t('settings:pdf.failed'),
      t('settings:pdf.retry'),
      t('settings:pdf.logPath', { path: '/tmp/x.log' }),
      t('settings:pdf.requestFailed'),
      t('settings:tabs.pdf'),
    ]
    for (const text of texts) {
      expect(text).not.toContain('{{')
      expect(text).not.toMatch(/^pdf\./)
    }
    expect(t('settings:pdf.progress', { pct: 42 })).toBe('約 42%')
  })
})

describe('部品が使えるようになった直後（/api/instance の取り直し前）', () => {
  // 部品は ready なのに、画面がまだ古い可否（pdf: false）を持っている一瞬。
  // 「まだ取り込めません」には戻さない。
  let t: TFunction
  beforeAll(async () => {
    const i18n = i18next.createInstance()
    await i18n.init({
      lng: 'ja',
      resources: { ja: { document: jaDocument } },
      interpolation: { escapeValue: false },
    })
    t = i18n.t
  })
  it('案内は「準備をしています」、置いたときは「入れ終わると取り込めます」', () => {
    expect(unavailableNotes(t, dNoPdf, rt('ready'))).toEqual(['PDF を読み取る準備をしています…'])
    expect(pdfInstallable(dNoPdf, rt('ready'))).toBe(false)
    expect(unavailableDropMessage(t, dNoPdf, 'pdf', rt('ready'))).toMatch(/入れ終わると取り込めます/)
  })
})
