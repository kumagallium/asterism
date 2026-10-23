// 変換中かを ref で追い、keydown が「変換の確定の Enter」かを判定するフック（`imeEnter.ts` の判定を使う）。
//
// 使い方:
//   const { compositionHandlers, isImeKey } = useImeEnterGuard()
//   <textarea {...compositionHandlers} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !isImeKey(e)) send() }} />
import { useCallback, useMemo, useRef } from 'react'
import { isImeKeyEvent } from './imeEnter'

interface GuardableKeyEvent {
  keyCode: number
  nativeEvent: { isComposing: boolean }
}

export function useImeEnterGuard() {
  const composingRef = useRef(false)
  const lastEndAtRef = useRef(0)
  const onCompositionStart = useCallback(() => {
    composingRef.current = true
  }, [])
  const onCompositionEnd = useCallback(() => {
    composingRef.current = false
    lastEndAtRef.current = Date.now()
  }, [])
  const isImeKey = useCallback(
    (e: GuardableKeyEvent) =>
      isImeKeyEvent({
        composingNow: composingRef.current,
        isComposing: e.nativeEvent.isComposing,
        keyCode: e.keyCode,
        msSinceCompositionEnd: Date.now() - lastEndAtRef.current,
      }),
    [],
  )
  const compositionHandlers = useMemo(() => ({ onCompositionStart, onCompositionEnd }), [onCompositionStart, onCompositionEnd])
  return { compositionHandlers, isImeKey }
}
