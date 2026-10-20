// 「数で見る」の表（「…」から開く）: 図と同じ内容（星座・星・件数・用語・標準用語）。読み上げ・コピー用。
import { useTranslation } from 'react-i18next'
import type { SkyData } from './skyApi'
import type { SkyTables as Tables } from './skyTable'

const fmt = (n: number): string => n.toLocaleString('en-US')

export function SkyTables({ tables, sky, onClose }: { tables: Tables; sky: SkyData; onClose: () => void }) {
  const { t } = useTranslation('sky')
  const none = <span className="sky-meta">—</span>
  const empty =
    tables.stars.length === 0 && tables.terms.length === 0 && tables.standards.length === 0 && tables.hubs.length === 0
  return (
    <section className="sky-table-wrap" aria-label={t('table.summary')}>
      <div className="sky-table-head">
        <span className="sky-table-counts">
          {[
            [sky.facts.kinds, 'facts.kinds'],
            [sky.facts.terms, 'facts.terms'],
            [sky.datasets.length, 'facts.datasets'],
            [sky.facts.triples, 'facts.triples'],
          ].map(([n, key]) => (
            <span key={key}>
              <b>{fmt(n as number)}</b>
              {t(key as string)}
            </span>
          ))}
        </span>
        <button type="button" className="btn btn--ghost btn--sm" onClick={onClose}>
          {t('table.close')}
        </button>
      </div>
      {empty ? (
        <p className="sky-hint">{t('table.empty')}</p>
      ) : (
        <div className="sky-tables">
          {tables.stars.length > 0 && (
            <div className="sky-table-scroll">
              <table className="sky-table">
                <caption>{t('table.stars')}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t('table.colDataset')}</th>
                    <th scope="col">{t('table.colKind')}</th>
                    <th scope="col">{t('table.colCount')}</th>
                    <th scope="col">{t('table.colTerms')}</th>
                    <th scope="col">{t('table.colStandard')}</th>
                  </tr>
                </thead>
                <tbody>
                  {tables.stars.map((r, i) => (
                    <tr key={`${r.dataset}-${r.iri}-${i}`}>
                      <td>{r.dataset}</td>
                      <td>{r.kind}</td>
                      <td className="num">{fmt(r.count)}</td>
                      <td>{r.terms.length ? r.terms.join('・') : none}</td>
                      <td>{r.standard ?? none}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {tables.terms.length > 0 && (
            <div className="sky-table-scroll">
              <table className="sky-table">
                <caption>{t('table.terms')}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t('table.colTerm')}</th>
                    <th scope="col">{t('table.colType')}</th>
                    <th scope="col">{t('table.colKinds')}</th>
                    <th scope="col">{t('table.colStandard')}</th>
                    <th scope="col">{t('table.colQuestions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {tables.terms.map((r) => (
                    <tr key={r.iri}>
                      <td>{r.label}</td>
                      <td>{r.type === 'property' ? t('table.typeProperty') : t('table.typeClass')}</td>
                      <td>{r.kinds.length ? r.kinds.join('・') : none}</td>
                      <td>{r.standard ?? none}</td>
                      <td>{r.questions.length ? r.questions.join('・') : none}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {tables.standards.length > 0 && (
            <div className="sky-table-scroll">
              <table className="sky-table">
                <caption>{t('table.standards')}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t('table.colStandard')}</th>
                    <th scope="col">{t('table.colBound')}</th>
                  </tr>
                </thead>
                <tbody>
                  {tables.standards.map((r) => (
                    <tr key={r.iri}>
                      <td>{r.label}</td>
                      <td>{r.bound.length ? r.bound.join('・') : none}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {tables.hubs.length > 0 && (
            <div className="sky-table-scroll">
              <table className="sky-table">
                <caption>{t('table.hubs')}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t('table.colHub')}</th>
                    <th scope="col">{t('table.colMembers')}</th>
                  </tr>
                </thead>
                <tbody>
                  {tables.hubs.map((r, i) => (
                    <tr key={`${r.label}-${i}`}>
                      <td>{r.label}</td>
                      <td>{r.members.length ? r.members.join('・') : none}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
