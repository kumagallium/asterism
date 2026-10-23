// 範囲の札（PR 11o）: 入力欄の上に「〈名前〉について」。× で外す。点か「文」の行を選ぶと付く。
import { useTranslation } from 'react-i18next'

export interface ScopeBarProps {
  name: string
  onClear: () => void
}

export function ScopeBar({ name, onClear }: ScopeBarProps) {
  const { t } = useTranslation('chat')
  return (
    <div className="chat-scope">
      <span className="chat-scope-chip" data-testid="scope-chip">
        {t('scope.about', { name })}
        <button type="button" className="chat-scope-x" data-testid="scope-clear" onClick={onClear} aria-label={t('scope.clear')} title={t('scope.clear')}>
          ×
        </button>
      </span>
    </div>
  )
}
