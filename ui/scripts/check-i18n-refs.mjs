#!/usr/bin/env node
// Keys referenced in src/ must exist in the ja locale.
//
// The parity check (check-i18n-parity.mjs) compares ja against en, so a key
// missing from BOTH locales passes it — and because ja is the fallback
// language, there is nothing left to fall back to: the raw key string is
// rendered on screen (PR #403: `serverKeys.save` shown as a button label).
// This check closes that blind spot by resolving every statically-written
// key reference in src/ against the ja resources.
//
// One direction only: references without a definition. The reverse (defined
// but never referenced) cannot be told apart from dynamic-key usage like
// t(`jobs:kind.${kind}`), so it is not attempted.
//
// What counts as a reference:
//   1. any 'ns:key' string literal where ns is a real namespace — covers
//      i18n.t('ns:key'), <Trans i18nKey="ns:key">, and ternary branches
//   2. bare t('key') — resolved with the file's useTranslation('ns')
//      namespace (default namespace 'common' when the file has none)
//   3. calls through getFixedT bindings — `const x = i18n.getFixedT(lng, 'ns')`
//      is picked up per file and x('key') resolves against that ns
//   4. template keys t(`prefix${…}`) — the literal prefix must match the
//      start of at least one defined key (allows step${n} → step1…step6)
//
// Misses by design: fully dynamic keys with no prefix, and bare-t ternaries
// inside the call — t(cond ? 'a' : 'b'). A false positive (a plain string
// that happens to look like 'ns:key') has not occurred; if one ever does,
// prefer renaming the string over teaching this script exceptions.
//
// Run: node scripts/check-i18n-refs.mjs   (part of npm run lint:i18n)

import { readFileSync, readdirSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const UI_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const SRC = join(UI_ROOT, 'src')
const LOCALE_JA = join(SRC, 'i18n', 'locales', 'ja')
const PLURAL_SUFFIX = /_(zero|one|two|few|many|other)$/

// --- ja definitions: leaf AND intermediate keys, plus plural-normalized aliases ---
const defined = new Map() // ns -> Set<dotted key>

function collect(value, prefix, out) {
  if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
    for (const [k, v] of Object.entries(value)) {
      const key = prefix ? `${prefix}.${k}` : k
      out.add(key)
      out.add(key.replace(PLURAL_SUFFIX, ''))
      collect(v, key, out)
    }
  }
  // strings and arrays are leaves; their own key was added by the parent step
}

for (const file of readdirSync(LOCALE_JA)) {
  if (!file.endsWith('.json')) continue
  const keys = new Set()
  collect(JSON.parse(readFileSync(join(LOCALE_JA, file), 'utf8')), '', keys)
  defined.set(file.slice(0, -'.json'.length), keys)
}

const has = (ns, key) =>
  defined.has(ns) && (defined.get(ns).has(key) || defined.get(ns).has(key.replace(PLURAL_SUFFIX, '')))
const hasPrefix = (ns, prefix) => {
  if (!defined.has(ns)) return false
  for (const k of defined.get(ns)) if (k.startsWith(prefix)) return true
  return false
}

// --- source scan ---
// (?<![\w.$]) keeps method calls like xs.at( and words like split( out of the
// bare-t match; static i18n.t(…) is intentionally left to the 'ns:key'
// literal pattern (every call site writes the namespace).
const NS_LITERAL = /(['"])([a-z][a-zA-Z0-9]*):([A-Za-z0-9_.]+)\1/g
const BARE_T = /(?<![\w.$])t\(\s*(['"])([^'"\n]+?)\1/g
const GET_FIXED = /\b(\w+)\s*=\s*i18n\.getFixedT\([^)]*?['"](\w+)['"]\s*\)/g
const DYNAMIC_T = /(?<![\w.$])(i18n\.)?t\(\s*`([^`]*?)\$\{/g
const USE_NS = /useTranslation\(\s*(?:\[\s*)?['"]([^'"]+)['"]/

function* sourceFiles(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, entry.name)
    if (entry.isDirectory()) yield* sourceFiles(p)
    else if (/\.tsx?$/.test(entry.name) && !entry.name.endsWith('.d.ts')) yield p
  }
}

const lineOf = (text, index) => text.slice(0, index).split('\n').length

const problems = []
let staticRefs = 0
let dynamicRefs = 0

for (const file of sourceFiles(SRC)) {
  const text = readFileSync(file, 'utf8')
  const fileNs = USE_NS.exec(text)?.[1] ?? 'common'
  const rel = relative(UI_ROOT, file)
  const report = (index, ref) => problems.push(`${rel}:${lineOf(text, index)}  ${ref}`)

  for (const m of text.matchAll(NS_LITERAL)) {
    const [, , ns, key] = m
    if (!defined.has(ns)) continue // 'div:hover' etc. — not a namespace
    staticRefs++
    if (!has(ns, key)) report(m.index, `${ns}:${key}`)
  }
  for (const m of text.matchAll(BARE_T)) {
    const key = m[2]
    if (key.includes(':')) continue // already handled as NS_LITERAL
    staticRefs++
    if (!has(fileNs, key)) report(m.index, `${fileNs}:${key} (bare t)`)
  }
  for (const [, name, ns] of text.matchAll(GET_FIXED)) {
    const call = new RegExp(String.raw`(?<![\w.$])${name}\(\s*(['"])([^'"\n]+?)\1`, 'g')
    const dyn = new RegExp(String.raw`(?<![\w.$])${name}\(\s*\`([^\`]*?)\$\{`, 'g')
    for (const m of text.matchAll(call)) {
      staticRefs++
      if (!has(ns, m[2])) report(m.index, `${ns}:${m[2]} (${name})`)
    }
    for (const m of text.matchAll(dyn)) {
      if (!m[1]) continue
      dynamicRefs++
      if (!hasPrefix(ns, m[1])) report(m.index, `${ns}:${m[1]}* (${name}, dynamic)`)
    }
  }
  for (const m of text.matchAll(DYNAMIC_T)) {
    let [, viaSingleton, prefix] = m
    let ns = viaSingleton ? 'common' : fileNs // i18n.t is not scoped by useTranslation
    const colon = prefix.indexOf(':')
    if (colon > 0 && defined.has(prefix.slice(0, colon))) {
      ns = prefix.slice(0, colon)
      prefix = prefix.slice(colon + 1)
    }
    if (!prefix) continue // t(`${…}`) — nothing static to check
    dynamicRefs++
    if (!hasPrefix(ns, prefix)) report(m.index, `${ns}:${prefix}* (dynamic)`)
  }
}

if (problems.length > 0) {
  console.error(`i18n refs: ${problems.length} reference(s) with no ja definition`)
  for (const p of problems.sort()) console.error(`  ${p}`)
  console.error('\nja is the fallback locale — a key missing there renders as the raw key string.')
  process.exit(1)
}

console.log(`i18n refs: ok (${staticRefs} static + ${dynamicRefs} dynamic-prefix refs resolve in ja)`)
