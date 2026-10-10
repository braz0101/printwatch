import type { SVGProps } from 'react'
import type { State } from './api'

const base: SVGProps<SVGSVGElement> = {
  viewBox: '0 0 24 24', width: 16, height: 16, fill: 'none', stroke: 'currentColor', strokeWidth: 2,
  strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true,
}
type P = SVGProps<SVGSVGElement>

export const IconCrit = (p: P) => <svg {...base} {...p}><circle cx="12" cy="12" r="9" /><path d="M12 7.5v5.5M12 16.5v.01" /></svg>
export const IconWarn = (p: P) => <svg {...base} {...p}><path d="M12 3.5 2.8 19.5h18.4L12 3.5Z" /><path d="M12 10v4.5M12 17.2v.01" /></svg>
export const IconDown = (p: P) => <svg {...base} {...p}><path d="M2 2l20 20M8.5 16.4a5 5 0 0 1 7 0M5 12.9a10 10 0 0 1 3.1-2M10.7 5.1A15 15 0 0 1 22 9M1.4 9a15 15 0 0 1 4.2-2.8M12 20h.01" /></svg>
export const IconOk = (p: P) => <svg {...base} {...p}><circle cx="12" cy="12" r="9" /><path d="m8 12.4 2.7 2.7L16.2 9.5" /></svg>
export const IconNoData = (p: P) => <svg {...base} {...p}><circle cx="12" cy="12" r="9" strokeDasharray="3 3" /></svg>
export const IconReplace = (p: P) => <svg {...base} {...p}><path d="M3 12a9 9 0 0 1 15.5-6.2L21 8M21 3v5h-5M21 12a9 9 0 0 1-15.5 6.2L3 16M3 21v-5h5" /></svg>
export const IconSync = (p: P) => <svg {...base} {...p}><path d="M3 12a9 9 0 0 1 15.5-6.2L21 8M21 3v5h-5M21 12a9 9 0 0 1-15.5 6.2L3 16M3 21v-5h5" /></svg>
export const IconPencil = (p: P) => <svg {...base} {...p}><path d="M4 20h4L19.5 8.5a2.8 2.8 0 0 0-4-4L4 16v4Z" /><path d="m13.5 6.5 4 4" /></svg>
export const IconTrash = (p: P) => <svg {...base} {...p}><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" /></svg>
export const IconSearch = (p: P) => <svg {...base} {...p}><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></svg>
export const IconChart = (p: P) => <svg {...base} {...p}><path d="M3 3v18h18M7 15l4-4 3 3 5-6" /></svg>
export const IconTable = (p: P) => <svg {...base} {...p}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 10h18M9 4v16" /></svg>

export const IconClose = (p: P) => <svg {...base} {...p}><path d="M6 6l12 12M18 6 6 18" /></svg>
export const IconBox = (p: P) => <svg {...base} {...p}><path d="M3 7.5 12 3l9 4.5v9L12 21l-9-4.5v-9Z" /><path d="m3 7.5 9 4.5 9-4.5M12 12v9" /></svg>
export const IconPlus = (p: P) => <svg {...base} {...p}><path d="M12 5v14M5 12h14" /></svg>
export const IconClock = (p: P) => <svg {...base} {...p}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>
export const IconArrow = (p: P) => <svg {...base} {...p}><path d="M5 12h14M13 6l6 6-6 6" /></svg>
export const IconUser = (p: P) => <svg {...base} {...p}><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></svg>
export const IconBell = (p: P) => <svg {...base} {...p}><path d="M6 9a6 6 0 1 1 12 0c0 6 2.5 7.5 2.5 7.5h-17S6 15 6 9ZM10 20a2 2 0 0 0 4 0" /></svg>

export const STATE_ICON: Record<State, (p: P) => React.ReactElement> = {
  CRIT: IconCrit, WARN: IconWarn, down: IconDown, OK: IconOk, NODATA: IconNoData,
}

/** Repère de calage quadrichromique : cyan, magenta, jaune et noir autour d'une cible. */
export function Logo({ size = 38 }: { size?: number }) {
  return (
    <svg viewBox="0 0 40 40" width={size} height={size} role="img" aria-label="PrintWatch">
      <circle cx="20" cy="20" r="17" fill="var(--surface)" stroke="var(--line)" strokeWidth="1.5" />
      <path d="M20 20 20 3a17 17 0 0 1 17 17Z" fill="var(--ink-cyan)" />
      <path d="M20 20h17a17 17 0 0 1-17 17Z" fill="var(--ink-magenta)" />
      <path d="M20 20v17A17 17 0 0 1 3 20Z" fill="var(--ink-yellow)" />
      <path d="M20 20H3A17 17 0 0 1 20 3Z" fill="var(--ink-black)" />
      <circle cx="20" cy="20" r="6.5" fill="var(--surface)" />
      <circle cx="20" cy="20" r="2.2" fill="var(--text)" />
    </svg>
  )
}
