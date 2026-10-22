/**
 * A dataset whose stored design the workbench should reopen for a redesign
 * (refine/edit → re-materialize in place). Passed by the catalog's "見直す"
 * action; null/undefined keeps the normal new-design flow.
 */
export interface RedesignTarget {
  datasetId: string
  datasetName: string
  proposalMd: string
  /**
   * The design weaknesses that motivated this review, carried across so the
   * review screen can say WHAT to fix. Without them the reviewer lands on the
   * column-meanings table with the finding gone — "I got told these aren't
   * linked, and now I'm looking at a list of columns" (user feedback,
   * 2026-07-24). Raw advisory text: it is what the AI fix needs; the screen
   * shows the plain-language face via `plainAdvisories`.
   */
  advisories?: string[]
}
