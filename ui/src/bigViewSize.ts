// 「大きく見る」の寸法・拡大縮小の設定（純関数と hook）。部品は BigView.tsx。
import { useEffect, useState } from 'react'

/** 大きく見るときの中身の高さの目安（窓の高さの 8 割・下限 360）。 */
export function bigViewHeight(innerHeight: number): number {
  return Math.max(360, Math.round(innerHeight * 0.8))
}

/** ホイール・ダブルクリックの拡大縮小の設定。省略時は従来どおり両方 false。 */
export function zoomFlags(zoomable?: boolean): { zoomOnScroll: boolean; zoomOnDoubleClick: boolean } {
  const on = zoomable === true
  return { zoomOnScroll: on, zoomOnDoubleClick: on }
}

/** 大きく見る重ね表示の中で、図以外（選んだものの帯・注意書き）に残す高さを引いた図の高さ。 */
export function bigGraphHeight(bigH: number, reserve = 160): number {
  return Math.max(320, bigH - reserve)
}

/** 窓の大きさに追従する {@link bigViewHeight}。 */
export function useBigViewHeight(): number {
  const [h, setH] = useState(() => bigViewHeight(window.innerHeight))
  useEffect(() => {
    const onResize = () => setH(bigViewHeight(window.innerHeight))
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  return h
}
