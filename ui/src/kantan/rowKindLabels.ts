import { basename } from '../skeletonContainment'

/** 行の種類の表示名 {ファイル名: 表示名}（K63）。拡張子を落として呼ぶが、落とすと
 *  同じ名前になるファイルどうしは拡張子まで含めて呼ぶ（見分けられなくなるので）。 */
export function rowKindLabels(
  names: string[],
  label: (file: string) => string,
): Record<string, string> {
  const shown = (n: string) => basename(n).replace(/\.[^.]+$/, '') || basename(n)
  const count = new Map<string, number>()
  for (const n of new Set(names)) count.set(shown(n), (count.get(shown(n)) ?? 0) + 1)
  return Object.fromEntries(
    [...new Set(names)].map((n) => [
      n,
      label((count.get(shown(n)) ?? 0) > 1 ? basename(n) : shown(n)),
    ]),
  )
}
