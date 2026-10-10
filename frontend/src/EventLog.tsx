import type { Ev } from './api'
import { IconCrit, IconDown, IconOk, IconReplace, IconWarn } from './Icons'
import { ago, fmtDate } from './ui'

type P = React.SVGProps<SVGSVGElement>
export const EVENT_META: Record<string, { label: string; Icon: (p: P) => React.ReactElement; color: string }> = {
  critical: { label: 'Niveau critique', Icon: IconCrit, color: 'var(--crit)' },
  warn: { label: 'Niveau bas', Icon: IconWarn, color: 'var(--warn)' },
  resolved: { label: 'Retour à la normale', Icon: IconOk, color: 'var(--ok)' },
  replaced: { label: 'Cartouche remplacée', Icon: IconReplace, color: 'var(--accent)' },
  down: { label: 'Injoignable', Icon: IconDown, color: 'var(--muted)' },
  up: { label: 'De nouveau joignable', Icon: IconOk, color: 'var(--ok)' },
}
const META_FALLBACK = { label: 'Événement', Icon: IconWarn, color: 'var(--muted)' }

export function EventItem({ e, now, showPrinter = true, onOpen }: { e: Ev; now: number; showPrinter?: boolean; onOpen?: () => void }) {
  const m = EVENT_META[e.type] ?? META_FALLBACK
  const body = (
    <>
      <span className="ev-ico" style={{ color: m.color, background: `color-mix(in srgb, ${m.color} 13%, transparent)` }} aria-hidden="true"><m.Icon width={15} height={15} /></span>
      <span className="min-w-0">
        <span className="block text-[13.5px] leading-snug">{showPrinter && <b className="font-semibold">{e.printer} · </b>}<span style={{ color: m.color === 'var(--warn)' ? 'var(--warn)' : undefined }}>{m.label}</span></span>
        <span className="muted block text-[12.5px] leading-snug">{e.message}</span>
        <span className="faint block text-[12px]" title={fmtDate(e.ts)}>{ago(e.ts, now)}</span>
      </span>
    </>
  )
  return <li>{onOpen ? <button type="button" className="ev" onClick={onOpen}>{body}</button> : <div className="ev">{body}</div>}</li>
}
