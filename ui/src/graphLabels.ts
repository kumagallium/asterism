import type { NodeLabelDrawingFunction } from 'sigma/rendering'

/** sigma の既定位置を保ち、文字の下だけ紙色で線を隠す。 */
export function makeGraphLabelDrawer(paper: string): NodeLabelDrawingFunction {
  return (context, data, settings) => {
    if (!data.label) return
    const size = settings.labelSize
    const x = data.x + data.size + 3
    const baseline = data.y + size / 3
    context.save()
    context.font = `${settings.labelWeight} ${size}px ${settings.labelFont}`
    context.textAlign = 'left'
    context.textBaseline = 'alphabetic'
    const metrics = context.measureText(data.label)
    // 実際の字形の高さが測れない環境では font size から補う。
    const ascent = metrics.actualBoundingBoxAscent ?? size
    const descent = metrics.actualBoundingBoxDescent ?? size / 4
    context.fillStyle = paper
    context.fillRect(x - 3, baseline - ascent - 2, metrics.width + 6, ascent + descent + 4)
    const color = settings.labelColor.attribute ? data[settings.labelColor.attribute] : undefined
    context.fillStyle = typeof color === 'string' ? color : settings.labelColor.color ?? '#16243b'
    context.fillText(data.label, x, baseline)
    context.restore()
  }
}
