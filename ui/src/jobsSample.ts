// アクティビティ（JobsView）の、見本の入れ替え（kind: sample_refresh）の行。
//
// この kind の行にはファイル名も「〇件の事実」も無い。何をしたかを 1〜2 文で言う
// （K4: コードのまま出さない。知らない action も総称の文に落とす）。
import type { IngestJob } from './jobsApi'

export type Translate = (key: string, options?: Record<string, unknown>) => string

export const SAMPLE_JOB_KIND = 'sample_refresh'

const KNOWN_ACTIONS = ['startup', 'override', 'restore'] as const

/** 利用者が変えた・追記したなど、「あなたが変えたので」と言ってよい理由。 */
const USER_REASONS = ['edited', 'decisions', 'appended', 'reingested']

export function isSampleJob(job: Pick<IngestJob, 'kind'>): boolean {
  return job.kind === SAMPLE_JOB_KIND
}

/** 状態のピルの文言。この kind の「partial」は失敗ではない（一部を入れ替えていない）。 */
export function sampleStatusKey(status: string): string | null {
  return status === 'partial' ? 'jobs:sample.status_partial' : null
}

/** 1 行に出す文（コードのままの識別子は入らない）。 */
export function sampleJobLines(job: IngestJob, t: Translate): string[] {
  const sample = job.sample
  const action = sample?.action ?? ''
  if (!(KNOWN_ACTIONS as readonly string[]).includes(action)) return [t('jobs:sample.other')]
  if (job.status === 'error') {
    return [t(action === 'restore' ? 'jobs:sample.restore_error' : 'jobs:sample.override_error')]
  }
  if (action === 'override') return [t('jobs:sample.override')]
  if (action === 'restore') return [t('jobs:sample.restore')]
  const lines: string[] = []
  if (sample && sample.units.length > 0) lines.push(t('jobs:sample.startup_updated'))
  if (sample && sample.held.length > 0) {
    const byYou = sample.held.every((h) => USER_REASONS.includes(h.reason))
    lines.push(t(byYou ? 'jobs:sample.startup_held_by_you' : 'jobs:sample.startup_held'))
  }
  return lines.length > 0 ? lines : [t('jobs:sample.other')]
}
