import { useTranslation } from 'react-i18next'
import { applyTableSpec, highlightStyleFor } from './tableSpec'
import type { Row, TableColumn, TableSpec } from './viewSpec'
import './table.css'

export interface TableViewProps {
  spec: TableSpec
  rows: Row[]
  ariaLabel: string
  onRowClick?: (row: Row) => void
}

// 生の IRI を短い名前に落とす（K4）。label があればそれを使う想定なので、
// ここは「表示名が無いときの最後の手段」。IRI 全体は title 属性へ。
function localName(iri: string): string {
  const cleaned = iri.replace(/[/#]+$/, '')
  const idx = Math.max(cleaned.lastIndexOf('/'), cleaned.lastIndexOf('#'))
  return idx >= 0 ? cleaned.slice(idx + 1) : cleaned
}

function formatCell(value: unknown, column: TableColumn): { text: string; title?: string } {
  if (value === null || value === undefined || value === '') return { text: '' }
  switch (column.format) {
    case 'number':
      return { text: new Intl.NumberFormat('ja-JP').format(Number(value)) }
    case 'integer':
      return { text: new Intl.NumberFormat('ja-JP', { maximumFractionDigits: 0 }).format(Number(value)) }
    case 'iri': {
      const iri = String(value)
      return { text: localName(iri), title: iri }
    }
    default:
      return { text: String(value) }
  }
}

function columnHeader(column: TableColumn) {
  if (!column.unit) return column.label
  return (
    <>
      {column.label} <span className="cardview-unit-suffix">[{column.unit}]</span>
    </>
  )
}

export function TableView({ spec, rows, ariaLabel, onRowClick }: TableViewProps) {
  const { t } = useTranslation('cards')
  const variant = spec.variant ?? 'grid'
  const prepared = applyTableSpec(spec, rows)

  if (prepared.length === 0) {
    return <p className="cardview-empty">{t('empty')}</p>
  }

  if (variant === 'figure') {
    const [first, ...rest] = spec.columns
    const row = prepared[0]
    const big = first ? formatCell(row[first.field], first) : { text: '' }
    return (
      <div className="cardview-figure" role="figure" aria-label={ariaLabel}>
        <p className="cardview-figure-big">
          {big.text}
          {first?.unit && <span className="cardview-unit-suffix">{first.unit}</span>}
        </p>
        {rest.map((col) => {
          const cell = formatCell(row[col.field], col)
          if (!cell.text) return null
          return (
            <p className="cardview-figure-sub" key={col.field}>
              {col.label}: <b title={cell.title}>{cell.text}</b>
            </p>
          )
        })}
      </div>
    )
  }

  if (variant === 'ranked') {
    // 生の IRI（subject_field）はセルとして描かない（K4）── 行の title と
    // onRowClick に渡すためだけに使う。
    return (
      <table className="cardview-rank" aria-label={ariaLabel}>
        <tbody>
          {prepared.map((row, i) => {
            const style = highlightStyleFor(row, spec.highlight)
            const subject = spec.subject_field ? row[spec.subject_field] : undefined
            return (
              <tr
                key={i}
                title={subject != null ? String(subject) : undefined}
                data-clickable={onRowClick ? '' : undefined}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
              >
                <td className="cardview-rank-i">{i + 1}</td>
                {spec.columns.map((col) => {
                  const cell = formatCell(row[col.field], col)
                  const isNum = col.format === 'number' || col.format === 'integer'
                  return (
                    <td
                      key={col.field}
                      className={
                        (isNum ? 'cardview-rank-num ' : '') +
                        (style ? `cardview-highlight--${style}` : '')
                      }
                    >
                      {cell.text}
                      {isNum && col.unit && <span className="cardview-unit-suffix"> {col.unit}</span>}
                    </td>
                  )
                })}
              </tr>
            )
          })}
        </tbody>
      </table>
    )
  }

  // grid
  return (
    <div className="cardview-table-wrap table-wrap">
      <table className="jobs-table sparql-table" aria-label={ariaLabel}>
        <thead>
          <tr>
            {spec.columns.map((col) => (
              <th key={col.field}>{columnHeader(col)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {prepared.map((row, i) => {
            const style = highlightStyleFor(row, spec.highlight)
            // `subject_field`（K4: 生の IRI を列に出さない）があれば行の
            // title と data-clickable に渡す — ranked と同じ流儀。
            const subject = spec.subject_field ? row[spec.subject_field] : undefined
            return (
              <tr
                key={i}
                title={typeof subject === 'string' ? subject : undefined}
                data-clickable={onRowClick ? '' : undefined}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
              >
                {spec.columns.map((col) => {
                  const cell = formatCell(row[col.field], col)
                  const isNum = col.format === 'number' || col.format === 'integer'
                  const isIri = col.format === 'iri'
                  // 兄弟の IRI 列（K4: 列としては出さない）の値があれば、
                  // このセルを `/describe` へのリンクにする（新しいタブ）。
                  const hrefIriRaw = col.href_field ? row[col.href_field] : undefined
                  const hrefIri = typeof hrefIriRaw === 'string' && hrefIriRaw ? hrefIriRaw : undefined
                  const href = hrefIri ? `/describe?iri=${encodeURIComponent(hrefIri)}` : undefined
                  return (
                    <td
                      key={col.field}
                      className={
                        (isNum ? 'cardview-cell-num ' : '') +
                        (isIri ? 'cardview-cell-iri ' : '') +
                        (style ? `cardview-highlight--${style}` : '')
                      }
                      title={href ? hrefIri : cell.title}
                    >
                      {href ? (
                        <a className="cardview-cell-link" href={href} target="_blank" rel="noreferrer">
                          {cell.text}
                        </a>
                      ) : (
                        cell.text
                      )}
                    </td>
                  )
                })}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
