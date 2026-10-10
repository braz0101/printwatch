import { useMemo, useState, type CSSProperties } from 'react'
import type { Printer, State, Status, Supply } from './api'
import { IconDown, IconPlus, IconSearch, IconWarn, STATE_ICON } from './Icons'
import { ALERT_COLOR, STATE_META, STATE_RANK, ago, alertText, inkColor, inkRank, isBox, isPresentOnly, lowestSupply, pretty, shownLevel, supplyName } from './ui'

interface Props {
  status: Status; filter: State | null; onFilter: (s: State | null) => void
  onOpen: (printer: string) => void
  /** Affiche le bouton « Ajouter une imprimante » (gestion activée côté serveur). */
  onAdd?: () => void
}

const CHIPS: State[] = ['CRIT', 'WARN', 'OK']

const isUnknown = (s: Supply) => s.kind === 'consumable' && s.pct == null && !isPresentOnly(s)
const isKnown = (s: Supply) => !s.stale && (s.pct != null || isPresentOnly(s))
/** Aucune donnée exploitable : imprimante injoignable, jamais relevée, ou aucun niveau remonté. */
const hasNoData = (p: Printer) => p.down || !p.supplies.some(isKnown)
/** Données partielles : au moins un niveau remonte, mais d'autres consommables restent inconnus. */
const isPartial = (p: Printer) => !hasNoData(p) && p.supplies.some(isUnknown)
/** Masquée par défaut : pas de données ou données partielles, sauf si une alerte est en cours (on ne cache jamais une alerte). */
const isHidden = (p: Printer) => (hasNoData(p) || isPartial(p)) && p.state !== 'CRIT' && p.state !== 'WARN'

/** État en simple pastille (icône + couleur) : le libellé reste disponible au survol et pour les lecteurs d'écran. */
function StateDot({ state }: { state: State }) {
  const m = STATE_META[state], I = STATE_ICON[state]
  return <span role="img" aria-label={m.label} title={m.label} className="state-dot" style={{ color: m.text, background: `color-mix(in srgb, ${m.color} 14%, transparent)` }}><I width={15} height={15} /></span>
}

const compactText = (s: Supply) => (isPresentOnly(s) ? 'Présente' : shownLevel(s) == null ? '—' : `${Math.round(shownLevel(s) as number)} %`)

/** Niveaux en barres colorées + pourcentage, sans libellé : la couleur de l'encre l'identifie (le nom reste au survol). */
function Bars({ p }: { p: Printer }) {
  const rows = p.supplies.filter((s) => !isUnknown(s)).sort((a, b) => inkRank(a) - inkRank(b) || (a.pct ?? 0) - (b.pct ?? 0))
  return (
    <ul className="grid grid-cols-2 gap-x-5 gap-y-2">
      {rows.map((s) => {
        const present = isPresentOnly(s), crit = !s.stale && s.alert === 'CRIT'
        return (
          <li key={s.key} className="flex items-center gap-2 text-[12.5px]" title={`${supplyName(s)} : ${compactText(s)}${s.stale ? ' (valeur périmée)' : ''}`}>
            {(isBox(s) || !s.color) && <span className="muted shrink-0 text-[11px]">{isBox(s) ? 'Récup.' : supplyName(s)}</span>}
            {present ? <span className="flex-1" /> : <span className={`minibar ${crit ? 'crit' : ''}`} style={{ '--c': s.stale ? 'var(--down)' : isBox(s) ? ALERT_COLOR[s.alert] : inkColor(s.color) } as CSSProperties}><i style={{ width: `${shownLevel(s) ?? 0}%` }} /></span>}
            <b className="w-[34px] shrink-0 text-right font-semibold tabular-nums" style={{ color: s.stale ? 'var(--muted)' : alertText(s.alert) }}>{compactText(s)}</b>
          </li>
        )
      })}
    </ul>
  )
}

/** Parc : une carte par imprimante, triées par urgence. Les imprimantes sans données sont repliées derrière un bouton. */
export default function FleetTable({ status, filter, onFilter, onOpen, onAdd }: Props) {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const needle = q.trim().toLowerCase()
  const match = (p: Printer) => !needle || [p.name, pretty(p.name), p.location, pretty(p.location), p.user, p.model, p.brand, p.id].some((x) => x?.toLowerCase().includes(needle))
  const hiddenAll = useMemo(() => status.printers.filter(isHidden), [status])
  const hiddenItems = useMemo(() => hiddenAll.filter(match).sort((a, b) => STATE_RANK[a.state] - STATE_RANK[b.state] || a.name.localeCompare(b.name, 'fr')),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [hiddenAll, needle])
  const pool = useMemo(() => status.printers.filter((p) => !isHidden(p)), [status])
  const items = useMemo(() => {
    const num = (a: number | null, b: number | null) => (a == null ? (b == null ? 0 : 1) : b == null ? -1 : a - b)   // valeurs absentes en dernier
    return pool
      .filter((p) => (!filter || p.state === filter) && match(p))
      .map((p) => ({ p, low: lowestSupply(p) }))
      .sort((a, b) => STATE_RANK[a.p.state] - STATE_RANK[b.p.state] || num(a.low?.pct ?? null, b.low?.pct ?? null) || a.p.name.localeCompare(b.p.name, 'fr'))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pool, filter, needle])

  const counts = useMemo(() => {
    const c: Record<State, number> = { CRIT: 0, WARN: 0, down: 0, OK: 0, NODATA: 0 }
    for (const p of pool) c[p.state]++
    return c
  }, [pool])

  return (
    <section aria-label="Imprimantes">
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <label className="relative block w-full max-w-xs">
          <IconSearch className="faint pointer-events-none absolute left-3 top-1/2 -translate-y-1/2" width={16} height={16} />
          <input value={q} onChange={(e) => setQ(e.target.value)} type="search" placeholder="Rechercher" aria-label="Rechercher une imprimante" className="search" />
        </label>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filtrer par état">
          <button type="button" className="chip-btn" aria-pressed={!filter} onClick={() => onFilter(null)}>Toutes <span className="chip-n">{pool.length}</span></button>
          {CHIPS.filter((s) => counts[s] > 0 || filter === s).map((s) => {
            const I = STATE_ICON[s]
            return <button key={s} type="button" className="chip-btn inline-flex items-center gap-1.5" aria-pressed={filter === s} onClick={() => onFilter(filter === s ? null : s)}>
              <I width={13} height={13} style={{ color: filter === s ? undefined : STATE_META[s].text }} />{STATE_META[s].plural}<span className="chip-n">{counts[s]}</span></button>
          })}
        </div>
        {onAdd && <button type="button" className="btn btn-primary ml-auto" onClick={onAdd} title="Ajouter une imprimante" aria-label="Ajouter une imprimante"><IconPlus width={15} height={15} />Ajouter</button>}
      </div>
      {(filter || q) && <p className="muted mb-2 text-[13px]" aria-live="polite">{items.length} résultat{items.length > 1 ? 's' : ''}
        <button type="button" className="link ml-3" onClick={() => { onFilter(null); setQ('') }}>Tout afficher</button></p>}

      {!items.length ? (hiddenItems.length && !filter ? null : <p className="muted panel py-16 text-center">Aucune imprimante ne correspond à ces critères.</p>)
        : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(270px,1fr))] gap-3">
            {items.map((it, i) => {
              const { p } = it
              const unknown = p.supplies.filter(isUnknown)
              return (
                <article key={p.id} className="panel card rise cursor-pointer p-3" style={{ '--delay': `${Math.min(i, 10) * 25}ms` } as CSSProperties}
                  aria-label={`${pretty(p.name)}, ${STATE_META[p.state].label}`} onClick={() => onOpen(p.id)}>
                  <header className="mb-3 flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <h3 className="truncate text-[15px] font-semibold leading-tight tracking-tight"><button type="button" className="row-link" onClick={(e) => { e.stopPropagation(); onOpen(p.id) }}>{pretty(p.name)}</button></h3>
                      {(p.user || p.location) && <p className="muted mt-0.5 truncate text-[12.5px]" title={[p.user, pretty(p.location), p.model].filter(Boolean).join(' · ')}>{[p.user, pretty(p.location)].filter(Boolean).join(' · ')}</p>}
                    </div>
                    <StateDot state={p.state} />
                  </header>
                  <Bars p={p} />
                  {unknown.length > 0 && (
                    <p className="mt-2.5 flex items-center gap-1 text-[12px]" style={{ color: 'var(--warn)' }} title={`Niveaux non remontés par l'imprimante : ${unknown.map(supplyName).join(', ')}. Aucune alerte n'est possible dessus.`}>
                      <IconWarn width={12} height={12} className="shrink-0" />{unknown.length} non remonté{unknown.length > 1 ? 's' : ''}
                    </p>
                  )}
                </article>
              )
            })}
          </div>
        )}

      {hiddenItems.length > 0 && !filter && (
        <div className="mt-6 flex flex-col items-center gap-4">
          <button type="button" className="chip-btn inline-flex items-center gap-2" aria-label="Afficher ou masquer les imprimantes sans données" aria-expanded={open || !!needle} aria-controls="sans-donnees" onClick={() => setOpen((v) => !v)}>
            <IconDown width={14} height={14} />
            Sans données<span className="chip-n">{hiddenItems.length}</span>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ transform: open || needle ? 'rotate(180deg)' : undefined, transition: 'transform .2s' }}><path d="m6 9 6 6 6-6" /></svg>
          </button>
          {(open || !!needle) && (
            <ul id="sans-donnees" className="grid w-full grid-cols-[repeat(auto-fill,minmax(270px,1fr))] gap-3">
              {hiddenItems.map((p) => (
                <li key={p.id} className="panel rise flex items-center justify-between gap-3 px-4 py-3">
                  <div className="min-w-0">
                    <button type="button" className="row-link truncate text-[15px] font-semibold" onClick={() => onOpen(p.id)}>{pretty(p.name)}</button>
                    <p className="muted truncate text-[12.5px]">{[p.user, p.down ? (p.last_seen ? `relevé ${ago(p.last_seen, status.generated_at)}` : 'jamais relevée') : 'niveaux non remontés'].filter(Boolean).join(' · ')}</p>
                  </div>
                  <StateDot state={hasNoData(p) && !p.down ? 'NODATA' : p.state} />
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  )
}
