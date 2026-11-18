// 外輪と内円は同じ picking index を使い、従来の丸全体をクリック対象として保つ。
import { createEdgeArrowProgram, createNodeCompoundProgram, NodeCircleProgram } from 'sigma/rendering'
import type { NodeDisplayData } from 'sigma/types'
import type { NodeLabelDrawingFunction } from 'sigma/rendering'
import { makeGraphLabelDrawer } from '../../graphLabels'

export function mapNodePrograms(paper: string | (() => string), drawLabel: NodeLabelDrawingFunction = makeGraphLabelDrawer(paper)) {
  class InnerCircle extends NodeCircleProgram {
    processVisibleItem(nodeIndex: number, offset: number, data: NodeDisplayData): void {
      super.processVisibleItem(nodeIndex, offset, { ...data, size: data.size * 0.62, color: typeof paper === 'function' ? paper() : paper })
    }
  }
  class CenterCircle extends NodeCircleProgram {
    processVisibleItem(nodeIndex: number, offset: number, data: NodeDisplayData): void {
      super.processVisibleItem(nodeIndex, offset, { ...data, size: data.size * 0.32 })
    }
  }
  return {
    'map-outline': createNodeCompoundProgram([NodeCircleProgram, InnerCircle], drawLabel, drawLabel),
    'map-filled': NodeCircleProgram,
    'map-dataset': createNodeCompoundProgram([NodeCircleProgram, InnerCircle, CenterCircle], drawLabel, drawLabel),
  }
}

/** 向きのある関係の線: 行き先（target）の点の手前で止まる小さな矢じり。大きさは線の太さに比例する（未確認の薄い線は小さく）。 */
export function mapEdgePrograms() {
  return { arrow: createEdgeArrowProgram({ lengthToThicknessRatio: 6, widenessToThicknessRatio: 3.5 }) }
}
