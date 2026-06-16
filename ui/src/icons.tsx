// Small inline stroke icons (no icon-library dependency). 24×24 viewBox,
// inherit currentColor so they take the surrounding text color. Matches the
// forest design kit (design_handoff_asterism_ux/prototype/kit.jsx).

import type { ReactNode } from 'react'

type IconProps = { className?: string; size?: number }

function Icon({
  className,
  size = 18,
  sw = 1.7,
  children,
}: IconProps & { sw?: number; children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={sw}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      {children}
    </svg>
  )
}

/** House — home / orientation. */
export function HomeIcon(p: IconProps) {
  return (
    <Icon {...p}>
      <path d="M4 11l8-7 8 7" />
      <path d="M6 10v9h12v-9" />
    </Icon>
  )
}

/** Plus — add data. */
export function AddIcon(p: IconProps) {
  return (
    <Icon {...p}>
      <path d="M12 5v14M5 12h14" />
    </Icon>
  )
}

/** Chat bubble — grounded question/answer. */
export function AskIcon(p: IconProps) {
  return (
    <Icon {...p}>
      <path d="M21 11.5a8.4 8.4 0 0 1-12.4 7.4L3 20.5l1.5-4.4A8.4 8.4 0 1 1 21 11.5z" />
    </Icon>
  )
}

/** 2×2 grid — catalog. */
export function CatalogIcon(p: IconProps) {
  return (
    <Icon {...p}>
      <rect x="3.5" y="3.5" width="7" height="7" rx="1.5" />
      <rect x="13.5" y="3.5" width="7" height="7" rx="1.5" />
      <rect x="3.5" y="13.5" width="7" height="7" rx="1.5" />
      <rect x="13.5" y="13.5" width="7" height="7" rx="1.5" />
    </Icon>
  )
}

/** Clock-with-arc — activity / ingest history. */
export function ActivityIcon(p: IconProps) {
  return (
    <Icon {...p}>
      <path d="M3 3v5h5" />
      <path d="M3.5 11a9 9 0 1 1 .5 4" />
      <path d="M12 7v5l3 2" />
    </Icon>
  )
}

/** Angle brackets — raw query / SPARQL (developer). */
export function CodeIcon(p: IconProps) {
  return (
    <Icon {...p}>
      <path d="M8 6l-5 6 5 6M16 6l5 6-5 6" />
    </Icon>
  )
}

/** Sparkle — AI proposal / regenerate. */
export function SparkIcon(p: IconProps) {
  return (
    <Icon {...p}>
      <path d="M12 3l1.7 4.9L18.6 9.6 13.7 11.4 12 16.3 10.3 11.4 5.4 9.6 10.3 7.9z" />
      <path d="M19 15l.6 1.8 1.8.6-1.8.6-.6 1.8-.6-1.8-1.8-.6 1.8-.6z" />
    </Icon>
  )
}

/** Check. */
export function CheckIcon(p: IconProps) {
  return (
    <Icon {...p}>
      <path d="M4 12l5 5L20 6" />
    </Icon>
  )
}

/** Right arrow. */
export function ArrowIcon(p: IconProps) {
  return (
    <Icon {...p}>
      <path d="M5 12h14M13 6l6 6-6 6" />
    </Icon>
  )
}

/** Chevron right. */
export function ChevronIcon(p: IconProps) {
  return (
    <Icon {...p}>
      <path d="M9 6l6 6-6 6" />
    </Icon>
  )
}

/** Two linked nodes — provenance trace. */
export function TraceIcon(p: IconProps) {
  return (
    <Icon {...p}>
      <circle cx="6" cy="6" r="2.4" />
      <circle cx="18" cy="18" r="2.4" />
      <path d="M8 8l8 8" />
    </Icon>
  )
}

/** Magnifier — inspect / search. */
export function SearchIcon(p: IconProps) {
  return (
    <Icon {...p}>
      <circle cx="11" cy="11" r="7" />
      <path d="M21 21l-4.3-4.3" />
    </Icon>
  )
}

/** Stacked layers — a dataset. */
export function LayersIcon(p: IconProps) {
  return (
    <Icon {...p}>
      <path d="M12 3l9 5-9 5-9-5z" />
      <path d="M3 13l9 5 9-5" />
    </Icon>
  )
}

/** Link — shared vocabulary. */
export function LinkIcon(p: IconProps) {
  return (
    <Icon {...p}>
      <path d="M9 15l6-6" />
      <path d="M11 6l1-1a4 4 0 0 1 6 6l-1 1" />
      <path d="M13 18l-1 1a4 4 0 0 1-6-6l1-1" />
    </Icon>
  )
}

/** Four connected nodes — connections / crosswalk (つながり). */
export function ConnectIcon(p: IconProps) {
  return (
    <Icon {...p}>
      <circle cx="6" cy="6" r="2.4" />
      <circle cx="18" cy="18" r="2.4" />
      <circle cx="18" cy="6" r="2.4" />
      <circle cx="6" cy="18" r="2.4" />
      <path d="M8.4 6H15.6M6 8.4V15.6M8.4 18H15.6M18 8.4V15.6" />
    </Icon>
  )
}

/** Document with lines — shared terms / vocabulary (共通の言葉). */
export function TermsIcon(p: IconProps) {
  return (
    <Icon {...p}>
      <path d="M5 4h11l3 3v13H5z" />
      <path d="M9 9h6M9 13h6M9 17h3" />
    </Icon>
  )
}

/** Globe — a world-wide external standard. */
export function GlobeIcon(p: IconProps) {
  return (
    <Icon {...p}>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18" />
      <path d="M12 3c2.6 2.7 2.6 15.3 0 18M12 3c-2.6 2.7-2.6 15.3 0 18" />
    </Icon>
  )
}

/** Document — an ingested file. */
export function FileIcon(p: IconProps) {
  return (
    <Icon {...p}>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5" />
    </Icon>
  )
}

/** Database cylinder — a dataset (データセット). */
export function DataIcon(p: IconProps) {
  return (
    <Icon {...p}>
      <ellipse cx="12" cy="6" rx="7.5" ry="3" />
      <path d="M4.5 6v12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3V6" />
      <path d="M4.5 12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3" />
    </Icon>
  )
}

/** Solid dot — small marker. */
export function DotIcon({ className, size = 12 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" className={className} aria-hidden="true">
      <circle cx="12" cy="12" r="5" fill="currentColor" />
    </svg>
  )
}

/** Brand mark — three stars connected (the asterism). */
export function BrandMark({ className, size = 26 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <line x1="5" y1="7" x2="18" y2="12" stroke="var(--border-strong)" strokeWidth="1.4" />
      <line x1="5" y1="7" x2="9" y2="19" stroke="var(--border-strong)" strokeWidth="1.4" />
      <line x1="18" y1="12" x2="9" y2="19" stroke="var(--border-strong)" strokeWidth="1.4" />
      <circle cx="5" cy="7" r="2.4" fill="currentColor" />
      <circle cx="18" cy="12" r="2.4" fill="currentColor" />
      <circle cx="9" cy="19" r="2.4" fill="currentColor" />
    </svg>
  )
}
