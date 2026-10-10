import type { CSSProperties } from 'react'
import type { Printer, Supply } from './api'
import { IconBox } from './Icons'
import { ALERT_COLOR, alertText, inkColor, inkRank, isBox, isPresentOnly, levelText, shownLevel, supplyName, useMounted } from './ui'

const stale_ = (s: Supply) => s.stale || s.pct == null

/** Éprouvette d'encre animée, avec repères des seuils (lus de la configuration). */
export function Tube({ s, warn, crit, delay = 0, onClick, pressed, showEta = true }: {
  s: Supply; warn: number; crit: number; delay?: number; onClick?: () => void; pressed?: boolean; showEta?: boolean
}) {
  const mounted = useMounted()
  const stale = stale_(s), isCrit = !stale && s.alert === 'CRIT'
  const inner = (
    <>
      <span className={`tube ${stale ? 'stale' : ''} ${isCrit ? 'crit' : ''}`} style={{ '--warn-at': `${warn}%`, '--crit-at': `${crit}%` } as CSSProperties}>
        <span className="tube-fill" style={{ '--c': inkColor(s.color), '--delay': `${delay}ms`, height: mounted ? `${s.pct ?? 0}%` : 0 } as CSSProperties} />
        <i className="tube-tick warn" /><i className="tube-tick crit" />
      </span>
      <span className="text-[15px] font-semibold leading-none" style={{ color: stale ? 'var(--muted)' : alertText(s.alert) }}>{levelText(s)}</span>
      <span className="muted w-full truncate text-center text-[12px] leading-none">{supplyName(s)}</span>
      {showEta && <span className="faint h-3 text-[11.5px] leading-none">{s.forecast && !stale ? `≈ ${Math.round(s.forecast.days_left)} j` : ''}</span>}
    </>
  )
  const label = `${supplyName(s)} : ${s.pct == null ? 'niveau inconnu' : `${Math.round(s.pct)} % restant`}${s.stale ? ', valeur périmée' : ''}`
  return onClick
    ? <button type="button" className="tube-col" onClick={onClick} aria-pressed={pressed} aria-label={label}>{inner}</button>
    : <div className="tube-col !cursor-default hover:!bg-transparent" role="img" aria-label={label}>{inner}</div>
}

/** Barre de remplissage : boîtier de maintenance, cartouche « présente » sans pourcentage, valeurs périmées. */
export function FillBar({ s, delay = 0, onClick, pressed }: { s: Supply; delay?: number; onClick?: () => void; pressed?: boolean }) {
  const mounted = useMounted()
  const stale = stale_(s), present = isPresentOnly(s), lvl = shownLevel(s)
  const body = (
    <>
      <span className="flex items-baseline justify-between gap-2 text-[13px]">
        <span className="flex min-w-0 items-center gap-2">
          {isBox(s) ? <IconBox width={14} height={14} className="muted shrink-0" /> : <i className="h-2.5 w-2.5 shrink-0 rounded-full ring-1 ring-black/10" style={{ background: inkColor(s.color) }} />}
          <span className="muted truncate">{supplyName(s)}</span>
        </span>
        <b className="shrink-0 font-semibold" style={{ color: stale ? 'var(--muted)' : alertText(s.alert) }}>{levelText(s)}</b>
      </span>
      {lvl != null && (
        <span className="bar-track">
          <span className="bar-fill" style={{ '--delay': `${delay}ms`, width: mounted ? `${lvl}%` : 0, background: stale ? 'repeating-linear-gradient(135deg, var(--ink-other) 0 5px, transparent 5px 9px)' : isBox(s) ? ALERT_COLOR[s.alert] : inkColor(s.color) } as CSSProperties} />
        </span>
      )}
      {present && <span className="faint text-[12px] leading-snug">Cartouche installée : l'imprimante n'indique pas la quantité restante.</span>}
    </>
  )
  const label = `${supplyName(s)} : ${levelText(s)}${s.stale ? ', valeur périmée' : ''}`
  return onClick
    ? <button type="button" className="tube-col !items-stretch !gap-1.5 px-3 py-2 text-left" onClick={onClick} aria-pressed={pressed} aria-label={label}>{body}</button>
    : <div className="tube-col !cursor-default !items-stretch !gap-1.5 px-3 py-2 text-left hover:!bg-transparent">{body}</div>
}

/** Rangée d'éprouvettes puis de barres pour une imprimante (carte et tiroir). */
export function SupplyGauges({ p, warn, crit, selected, onPick, delay = 0 }: {
  p: Printer; warn: number; crit: number; selected?: string | null; onPick?: (key: string) => void; delay?: number
}) {
  const asTube = (s: Supply) => !p.down && s.kind === 'consumable' && s.pct != null
  const tubes = p.supplies.filter(asTube).sort((a, b) => inkRank(a) - inkRank(b))
  const bars = p.supplies.filter((s) => !asTube(s))
  return (
    <>
      {tubes.length > 0 && (
        <div className="tubes">
          {tubes.map((s, i) => <Tube key={s.key} s={s} warn={warn} crit={crit} delay={delay + i * 90} onClick={onPick && (() => onPick(s.key))} pressed={selected === s.key} />)}
        </div>
      )}
      {bars.map((s, i) => (
        <div key={s.key} className={tubes.length ? 'mt-2' : ''}>
          <FillBar s={s} delay={delay + 300 + i * 90} onClick={onPick && (() => onPick(s.key))} pressed={selected === s.key} />
        </div>
      ))}
    </>
  )
}

/** Mini-éprouvettes pour le tableau du parc : une par encre, plus un repère pour le boîtier ou la cartouche « présente ». */
export function MicroTubes({ p }: { p: Printer }) {
  const mounted = useMounted()
  const meas = p.supplies.filter((s) => s.kind === 'consumable' && s.pct != null).sort((a, b) => inkRank(a) - inkRank(b))
  const box = p.supplies.find(isBox)
  const present = p.supplies.some(isPresentOnly)
  if (!meas.length && !box && !present) return <span className="faint text-[12.5px]">—</span>
  return (
    <span className="flex items-end gap-1" role="img"
      aria-label={p.supplies.map((s) => `${supplyName(s)} ${levelText(s)}`).join(', ')}>
      {meas.map((s, i) => (
        <span key={s.key} className={`micro ${p.down || s.stale ? 'stale' : ''} ${!p.down && s.alert === 'CRIT' ? 'crit' : ''}`} title={`${supplyName(s)} : ${levelText(s)}`}>
          <span style={{ background: inkColor(s.color), height: mounted ? `${s.pct}%` : 0, transitionDelay: `${i * 60}ms` }} />
        </span>
      ))}
      {box && (
        <span className="ml-1 flex items-center gap-1" title={`${supplyName(box)} : ${levelText(box)}`}>
          <IconBox width={13} height={13} className="muted" />
          <span className="text-[12px] font-semibold" style={{ color: p.down || box.stale ? 'var(--muted)' : alertText(box.alert) }}>{box.pct == null ? '—' : `${Math.round(100 - box.pct)}%`}</span>
        </span>
      )}
      {present && !meas.length && <span className="pill">Cartouche présente</span>}
    </span>
  )
}
