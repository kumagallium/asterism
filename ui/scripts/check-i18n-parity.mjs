#!/usr/bin/env node
// ja/en key parity for src/i18n/locales.
//
// i18next falls back to ja, so a key added to ja alone does not fail anywhere —
// it silently prints Japanese inside the English UI. The ADR (§5) says both
// locales are written in the same change; this makes a machine say so.
//
// Plural suffixes are normalized away before comparing: `turns_one` /
// `turns_other` (en) and `turns` (ja) are the SAME key — Japanese has one plural
// form, English has two, and that is correct in both.
//
// Run: node scripts/check-i18n-parity.mjs   (npm run lint:i18n)

import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const LOCALES_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'i18n', 'locales')
const PLURAL_SUFFIX = /_(zero|one|two|few|many|other)$/

/** Every leaf key of one namespace, dotted, with plural suffixes normalized. */
function leafKeys(value, prefix, out) {
  if (Array.isArray(value)) {
    // An array of strings is ONE authored unit (e.g. ask:examples.items) — its
    // length may legitimately differ per language, so compare the array itself.
    out.add(prefix)
    return out
  }
  if (value !== null && typeof value === 'object') {
    for (const [k, v] of Object.entries(value)) {
      const key = prefix ? `${prefix}.${k}` : k
      leafKeys(v, key.replace(PLURAL_SUFFIX, ''), out)
    }
    return out
  }
  out.add(prefix)
  return out
}

function namespacesOf(lng) {
  const dir = join(LOCALES_DIR, lng)
  const out = new Map()
  for (const file of readdirSync(dir)) {
    if (!file.endsWith('.json')) continue
    const ns = file.slice(0, -'.json'.length)
    out.set(ns, leafKeys(JSON.parse(readFileSync(join(dir, file), 'utf8')), '', new Set()))
  }
  return out
}

const ja = namespacesOf('ja')
const en = namespacesOf('en')
const problems = []

for (const ns of new Set([...ja.keys(), ...en.keys()])) {
  const jaKeys = ja.get(ns)
  const enKeys = en.get(ns)
  if (!jaKeys) {
    problems.push(`${ns}: ja/${ns}.json is missing`)
    continue
  }
  if (!enKeys) {
    problems.push(`${ns}: en/${ns}.json is missing`)
    continue
  }
  for (const k of jaKeys) if (!enKeys.has(k)) problems.push(`${ns}: missing in en — ${k}`)
  for (const k of enKeys) if (!jaKeys.has(k)) problems.push(`${ns}: missing in ja — ${k}`)
}

if (problems.length > 0) {
  console.error(`i18n parity: ${problems.length} problem(s)`)
  for (const p of problems.sort()) console.error(`  ${p}`)
  console.error('\nEvery key must exist in BOTH ja and en (see ADR kantan-mode-two-tier-ux §5).')
  process.exit(1)
}

console.log(`i18n parity: ok (${ja.size} namespaces)`)
