// 会話の列の場所（13b）: 論文を開いているあいだ、会話の列（スレッド＋入力欄）は**閉じずに隠す**。
// 閉じると入力欄の下書きが消えるので、`hidden` にして mount したままにする。
import type { ReactNode } from 'react'

export function ReaderSlot({ reader, pane }: { reader: ReactNode | null; pane: ReactNode }) {
  return (
    <>
      {reader}
      <div className="chat-pane-slot" data-testid="pane-slot" hidden={!!reader}>
        {pane}
      </div>
    </>
  )
}
