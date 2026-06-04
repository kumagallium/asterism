import { BIND_INFO, SHARED_VOCAB, VOCAB_USERS, type BindStrategy } from './datasetsApi'
import { ActivityIcon, ArrowIcon, LinkIcon } from './icons'

/**
 * Shared vocabulary board (design_handoff_asterism_ux #6). The answer to "if
 * datasets are primary, do ontology/mapping disappear?" — No: the vocabulary
 * stays first-class, it is just SHARED across datasets. Shows the shared classes,
 * which datasets use them and HOW they bind in (4 strategies), and why edits
 * ripple downstream.
 */
export function SharedVocabView({ onBack }: { onBack?: () => void }) {
  return (
    <div className="vocab">
      {onBack && (
        <button type="button" className="link-btn vocab-back" onClick={onBack}>
          <ArrowIcon size={14} className="vocab-back-arrow" /> カタログに戻る
        </button>
      )}

      <div className="vocab-banner">
        <span className="vocab-banner-icon">
          <LinkIcon size={19} />
        </span>
        <div>
          <div className="vocab-banner-title">
            「設計図（語彙）」は無くなりません — <span className="vocab-banner-hl">共有</span>されるだけ
          </div>
          <div className="vocab-banner-sub">
            データセットを主役にしても、語彙と取り込みルールは各データセットの中に残ります。
            ここはそのうち<strong>みんなで共通して使う部分</strong>。揃えるほど横断検索・比較が効きます。
          </div>
        </div>
      </div>

      <div className="vocab-grid">
        {/* shared classes */}
        <div className="card vocab-classes">
          <div className="vocab-card-head">
            <h3 className="card-h">共有クラス</h3>
            <span className="vocab-card-meta">
              {SHARED_VOCAB.classes.length} · {SHARED_VOCAB.name} {SHARED_VOCAB.version}
            </span>
          </div>
          <div className="vocab-class-list">
            {SHARED_VOCAB.classes.map((c) => (
              <div key={c.en} className="vocab-class">
                <div className="vocab-class-body">
                  <div className="vocab-class-title">
                    <span className="vocab-class-ja">{c.ja}</span>
                    <code className="vocab-class-en">{c.en}</code>
                  </div>
                  <div className="vocab-class-desc">{c.desc}</div>
                </div>
                <span className="vocab-class-users">
                  <span className="mono-strong">{c.users}</span> 利用
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* who uses it + how */}
        <div className="card vocab-users">
          <div className="vocab-card-head">
            <h3 className="card-h">どのデータが、どう使っているか</h3>
            <span className="vocab-legend">
              {(Object.keys(BIND_INFO) as BindStrategy[]).map((k) => (
                <BindChip key={k} strat={k} />
              ))}
            </span>
          </div>
          <div className="vocab-user-list">
            {VOCAB_USERS.map((u) => (
              <div key={u.name} className="vocab-user">
                <div className="vocab-user-head">
                  <span className="vocab-user-icon">
                    <LinkIcon size={14} />
                  </span>
                  <span className="vocab-user-name">{u.name}</span>
                  <span className="vocab-user-src">{u.src}</span>
                </div>
                <div className="vocab-user-binds">
                  {u.binds.map((b) => (
                    <span key={b.cls} className="vocab-bind">
                      <span className="vocab-bind-cls">{b.cls}</span>
                      <BindChip strat={b.strat} />
                    </span>
                  ))}
                </div>
              </div>
            ))}

            <div className="vocab-caution">
              <span className="vocab-caution-icon">
                <ActivityIcon size={16} />
              </span>
              <div>
                <strong>なぜ「要注意」？</strong>{' '}
                共有クラスを書き換えると、それを使う <span className="mono-strong">{VOCAB_USERS.length}</span>{' '}
                データセットすべての検索・回答に波及します。変更は<strong>影響範囲のプレビュー</strong>
                を見てから確定します。
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function BindChip({ strat }: { strat: BindStrategy }) {
  const b = BIND_INFO[strat]
  return (
    <span className={`bind-chip bind-chip--${b.tone}`}>
      {b.label}
      <span className="bind-chip-en">{b.en}</span>
    </span>
  )
}
