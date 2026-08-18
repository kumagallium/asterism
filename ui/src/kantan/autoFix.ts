/** Should the machine take this defect stop, or does it belong to the human?
 *
 * The wizard used to render a card whose only exit was a button that runs the
 * refine chain — the very thing the machine can run itself. The human had no
 * information the machine lacked; they pressed it blind, 2-5 times per import
 * (live 2026-08-16..18). So the wizard presses it. This is the whole decision,
 * kept pure so it can be exercised without a browser.
 *
 * Handing it back is right in exactly three cases: nothing actionable to say,
 * the budget is spent, or the SAME findings came back — a model that could not
 * move this set will not move it next time, and the card is then genuinely
 * worth a human's attention.
 */
export function shouldAutoFix(params: {
  lines: string[]
  budgetLeft: number
  lastKey: string | null
  busy: boolean
  hasDesign: boolean
}): { fix: boolean; key: string } {
  const key = params.lines.join('\n')
  const fix =
    params.lines.length > 0 &&
    params.budgetLeft > 0 &&
    key !== params.lastKey &&
    !params.busy &&
    params.hasDesign
  return { fix, key }
}
