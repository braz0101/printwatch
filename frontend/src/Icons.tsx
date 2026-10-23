import type { SVGProps } from 'react'
import type { Kind } from './derive'

const base: SVGProps<SVGSVGElement> = {
  viewBox: '0 0 24 24', width: 16, height: 16, fill: 'none', stroke: 'currentColor', strokeWidth: 2,
  strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true,
}
type P = SVGProps<SVGSVGElement>

export const IconCrit = (p: P) => <svg {...base} {...p}><path d="M8.2 2.5h7.6l5.7 5.7v7.6l-5.7 5.7H8.2l-5.7-5.7V8.2z" /><path d="M12 7.5v5.5M12 16.5v.01" /></svg>
export const IconWarn = (p: P) => <svg {...base} {...p}><path d="M12 3.5 2.8 19.5h18.4z" /><path d="M12 10v4.5M12 17.2v.01" /></svg>
export const IconTool = (p: P) => <svg {...base} {...p}><path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z" /></svg>
export const IconDown = (p: P) => <svg {...base} {...p}><path d="M2 2l20 20M8.5 16.4a5 5 0 0 1 7 0M5 12.9a10 10 0 0 1 3.1-2M10.7 5.1A15 15 0 0 1 22 9M1.4 9a15 15 0 0 1 4.2-2.8M12 20h.01" /></svg>
export const IconOk = (p: P) => <svg {...base} {...p}><circle cx="12" cy="12" r="9" /><path d="m8 12.4 2.7 2.7 5.5-5.6" /></svg>
export const IconNoData = (p: P) => <svg {...base} {...p}><circle cx="12" cy="12" r="9" strokeDasharray="3 3" /></svg>
export const IconClock = (p: P) => <svg {...base} {...p}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>
export const IconReplace = (p: P) => <svg {...base} {...p}><path d="M3 12a9 9 0 0 1 15.5-6.2L21 8M21 3v5h-5M21 12a9 9 0 0 1-15.5 6.2L3 16M3 21v-5h5" /></svg>
export const IconSync = IconReplace
export const IconPencil = (p: P) => <svg {...base} {...p}><path d="M4 20h4L19.5 8.5a2.8 2.8 0 0 0-4-4L4 16v4z" /><path d="m13.5 6.5 4 4" /></svg>
export const IconTrash = (p: P) => <svg {...base} {...p}><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" /></svg>
export const IconSearch = (p: P) => <svg {...base} {...p}><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
export const IconClose = (p: P) => <svg {...base} {...p}><path d="M6 6l12 12M18 6 6 18" /></svg>
export const IconPlus = (p: P) => <svg {...base} {...p}><path d="M12 5v14M5 12h14" /></svg>
export const IconDownload = (p: P) => <svg {...base} {...p}><path d="M12 3v12M7 10l5 5 5-5M4 19h16" /></svg>
export const IconScreen = (p: P) => <svg {...base} {...p}><rect x="2.5" y="4" width="19" height="13" rx="1.5" /><path d="M8 21h8M12 17v4" /></svg>
export const IconChevron = (p: P) => <svg {...base} {...p}><path d="m9 6 6 6-6 6" /></svg>
export const IconUser = (p: P) => <svg {...base} {...p}><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></svg>
export const IconSliders = (p: P) => <svg {...base} {...p}><path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0" /><circle cx="16" cy="6" r="2" /><circle cx="10" cy="12" r="2" /><circle cx="18" cy="18" r="2" /></svg>
export const IconLogout =(p: P) => <svg {...base} {...p}><path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4M9 16l-4-4 4-4M5 12h11" /></svg>
export const IconLock = (p: P) => <svg {...base} {...p}><rect x="4.5" y="10.5" width="15" height="10" rx="1.5" /><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3" /></svg>

export const KIND_ICON: Record<Kind, (p: P) => React.ReactElement> = {
  CRIT: IconCrit, INC: IconTool, WARN: IconWarn, OK: IconOk, DOWN: IconDown, NODATA: IconNoData,
}

/** Disque CMJN de PrintWatch (même dessin que l'icône d'onglet). */
export function LogoDisc({ size = 28 }: { size?: number }) {
  return (
    <svg viewBox="0 0 40 40" width={size} height={size} aria-hidden="true">
      <path d="M20 20V3a17 17 0 0 1 17 17Z" fill="#0098c4" />
      <path d="M20 20h17a17 17 0 0 1-17 17Z" fill="#d1227b" />
      <path d="M20 20v17A17 17 0 0 1 3 20Z" fill="#f2b200" />
      <path d="M20 20H3A17 17 0 0 1 20 3Z" fill="#26323e" />
      <circle cx="20" cy="20" r="6" fill="#fff" />
      <circle cx="20" cy="20" r="2.6" fill="#26323e" />
    </svg>
  )
}

/** Quatre carrés : cyan, magenta, jaune, noir. */
export function Logo({ size = 26 }: { size?: number }) {
  return (
    <svg viewBox="0 0 26 26" width={size} height={size} aria-hidden="true">
      <rect x="1" y="1" width="11" height="11" rx="2" fill="var(--ink-cyan)" />
      <rect x="14" y="1" width="11" height="11" rx="2" fill="var(--ink-magenta)" />
      <rect x="1" y="14" width="11" height="11" rx="2" fill="var(--ink-yellow)" />
      <rect x="14" y="14" width="11" height="11" rx="2" fill="var(--ink-black)" />
    </svg>
  )
}
