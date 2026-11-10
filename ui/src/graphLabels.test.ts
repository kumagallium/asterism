import { describe, expect, it, vi } from 'vitest'
import type { Settings } from 'sigma/settings'
import { makeGraphLabelDrawer } from './graphLabels'

const settings = { labelSize: 12, labelFont: '"Noto Sans JP", sans-serif', labelWeight: '400', labelColor: { color: '#16243b' } } as Settings

function context() {
  return { save: vi.fn(), restore: vi.fn(), measureText: vi.fn(() => ({ width: 100, actualBoundingBoxAscent: 10, actualBoundingBoxDescent: 2 })), fillRect: vi.fn(), fillText: vi.fn(), fillStyle: '', font: '', textAlign: '', textBaseline: '' }
}

describe('グラフの文字の地', () => {
  it('名前の既定位置とフォントを保ち、字形の周りだけを地で覆ってから文字を描く', () => {
    const ctx = context()
    makeGraphLabelDrawer('#fbfcfe')(ctx as unknown as CanvasRenderingContext2D, { x: 20, y: 30, size: 8, label: '世界の国', color: '#68778e' }, settings)
    expect(ctx.font).toBe('400 12px "Noto Sans JP", sans-serif')
    expect(ctx.fillRect).toHaveBeenCalledWith(28, 22, 106, 16)
    expect(ctx.fillText).toHaveBeenCalledWith('世界の国', 31, 34)
    expect(ctx.fillRect.mock.invocationCallOrder[0]).toBeLessThan(ctx.fillText.mock.invocationCallOrder[0])
    expect(ctx.save).toHaveBeenCalledOnce()
    expect(ctx.restore).toHaveBeenCalledOnce()
  })
  it('表示しない名前には地も描かない', () => {
    const ctx = context()
    makeGraphLabelDrawer('#ffffff')(ctx as unknown as CanvasRenderingContext2D, { x: 0, y: 0, size: 8, label: '', color: '#002869' }, settings)
    expect(ctx.fillRect).not.toHaveBeenCalled()
    expect(ctx.fillText).not.toHaveBeenCalled()
  })
})
