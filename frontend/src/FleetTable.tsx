import { Fragment, useEffect, useMemo, useState } from 'react'
import type { Printer, Status, Supply } from './api'
import { DEFAULT_FILTER, type Filter, type GroupBy, type Kind, INK_LETTER, inFilter, issuesOf, INK_ORDER, KIND_LABEL, KIND_PLURAL, download, etaOf, fmtDays, groupKey, kindOf, placeOf, sortByUrgency, toCsv } from './derive'
import { IconDownload, IconPlus, KIND_ICON } from './Icons'
import { DAY, INK_LABEL, ago, fmtDate, isBox, pretty } from './ui'

interface Props {
  status: Status; query: string; filter: Filter | null; onFilter: (k: Filter | null) => void
  onOpen: (id: string) => void; onAdd?: () => void
}

const CHIPS: Kind[] = ['CRIT', 'INC', 'WARN', 'DOWN', 'OK']

export function KindChip({ k }: { k: Kind }) {
  const I = KIND_ICON[k]
  return <span className="chip" data-k={k}><I width={13} height={13} strokeWidth={2.4} />{KIND_LABEL[k]}</span>
}

const valClass = (s: Supply) => (s.alert === 'CRIT' ? 'v-crit' : s.alert === 'WARN' ? 'v-warn' : '')
const inks = (p: Printer) => INK_ORDER.map((c) => p.supplies.find((s) => s.color === c && !isBox(s) && !s.stale)).filter((s): s is Supply => !!s)

function Inks({ p }: { p: Printer }) {
  const list = inks(p)
  if (!list.length) return <span className="faint" style={{ fontSize: 12.5 }}>Niveaux non remontés</span>
  return (
    <span className="inks">
      {list.map((s) => {
        const txt = s.pct == null ? (s.status === 'some_remaining' ? 'présente' : 'inconnu') : `${Math.round(s.pct)} %`
        return (
          <span key={s.key} className="ink" title={`${INK_LABEL[s.color!]} : ${txt}${s.tracked === false ? ' (non suivie : aucune alerte)' : ''}`}>
            <span className="ink-l">{INK_LETTER[s.color!]}</span>
            {s.pct == null ? <span className="faint" style={{ width: 60, fontSize: 12 }}>{txt}</span> : <>
              <span className="minibar"><i style={{ width: `${s.pct}%`, '--c': `var(--ink-${s.color})` } as React.CSSProperties} /></span>
              <span className={`ink-v ${valClass(s)}`}>{Math.round(s.pct)}</span>
            </>}
          </span>
        )
      })}
    </span>
  )
}

function downText(p: Printer, now: number) {
  if (!p.last_seen) return 'Jamais relevée'
  const d = now - p.last_seen
  return `Sans réponse depuis ${d < DAY ? `${Math.max(1, Math.round(d / 3600))} h` : `${Math.round(d / DAY)} j`}`
}

function boxCell(p: Printer) {
  const b = p.supplies.find((s) => isBox(s) && !s.stale)
  if (!b || b.pct == null) return <span className="faint">—</span>
  return <span className={b.alert === 'CRIT' ? 'v-crit' : b.alert === 'WARN' ? 'v-warn' : ''}>{Math.round(100 - b.pct)} %</span>
}

function etaCell(p: Printer, k: Kind) {
  if (k === 'DOWN') return <span className="faint">—</span>
  const e = etaOf(p)
  if (e == null) return <span className="faint">—</span>
  return <span className={e <= 7 ? 'v-crit' : e <= 14 ? 'v-warn' : 'muted'}>{fmtDays(e)}</span>
}

function useNarrow(px = 820) {
  const q = `(max-width: ${px}px)`
  const [n, setN] = useState(() => window.matchMedia?.(q).matches ?? false)
  useEffect(() => { const m = window.matchMedia(q); const f = () => setN(m.matches); m.addEventListener('change', f); return () => m.removeEventListener('change', f) }, [q])
  return n
}

/** Le parc : tableau trié par urgence (cartes sur téléphone). Les imprimantes injoignables restent visibles, grisées. */
export default function FleetTable({ status, query, filter, onFilter, onOpen, onAdd }: Props) {
  const [group, setGroup] = useState<GroupBy>('none')
  const narrow = useNarrow()
  const now = status.generated_at
  const showIp = status.printers.some((p) => p.host)
  const showBox = status.printers.some((p) => p.supplies.some(isBox))     // colonne masquée si aucun modèle du parc n'en remonte
  const cols = 5 + (showIp ? 1 : 0) + (showBox ? 1 : 0)
  const needle = query.trim().toLowerCase()
  const match = (p: Printer) => !needle || [p.name, pretty(p.name), p.location, pretty(p.location), p.user, p.model, p.brand, p.id, p.host,
    p.info?.serial, p.info?.mac, p.info?.sys_name, p.info?.model].some((x) => x?.toLowerCase().includes(needle))

  const all = useMemo(() => [...status.printers].sort(sortByUrgency), [status])
  const counts = useMemo(() => { const c: Record<Kind, number> = { CRIT: 0, INC: 0, WARN: 0, OK: 0, DOWN: 0, NODATA: 0 }; for (const p of all) c[kindOf(p)]++; return c }, [all])
  const count = (k: Kind) => counts[k] + (k === 'DOWN' ? counts.NODATA : 0)        // « sans relevé » rangé avec les injoignables
  const rows = all.filter((p) => inFilter(p, filter) && match(p))
  const alerts = counts.CRIT + counts.INC + counts.WARN
  const groups = useMemo(() => {
    if (group === 'none') return [{ name: '', items: rows }]
    const m = new Map<string, Printer[]>()
    for (const p of rows) { const g = groupKey(p, group); m.set(g, [...(m.get(g) ?? []), p]) }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0], 'fr')).map(([name, items]) => ({ name, items }))
  }, [rows, group])

  const exportCsv = () => download(`printwatch-parc-${new Date().toISOString().slice(0, 10)}.csv`, toCsv(rows))

  return (
    <section aria-labelledby="fleet-t" className="panel" style={{ overflow: 'hidden' }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '12px 16px', padding: '14px 20px', borderBottom: '1px solid var(--line)' }}>
        <h2 id="fleet-t" className="h2" style={{ marginRight: 8 }}>Parc</h2>
        <div role="group" aria-label="Filtrer par état" style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          <button type="button" className="seg" aria-pressed={filter === 'ALERT'} onClick={() => onFilter('ALERT')}
            title="Imprimantes en état critique, en attention ou avec un incident matériel">Critiques et attention <span className="seg-n">{alerts}</span></button>
          <button type="button" className="seg" aria-pressed={!filter} onClick={() => onFilter(null)}>Toutes <span className="seg-n">{all.length}</span></button>
          {CHIPS.filter((k) => count(k) > 0 || filter === k).map((k) => (
            <button key={k} type="button" className="seg" aria-pressed={filter === k} onClick={() => onFilter(filter === k ? DEFAULT_FILTER : k)}>
              {KIND_PLURAL[k]} <span className="seg-n">{count(k)}</span></button>
          ))}
        </div>
        <div className="no-print" style={{ marginLeft: 'auto', display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--text-2)' }}>Grouper
            <select className="select" value={group} onChange={(e) => setGroup(e.target.value as GroupBy)}>
              <option value="none">Aucun</option><option value="location">Emplacement</option><option value="model">Modèle</option>
              {showIp && <option value="subnet">Sous-réseau</option>}
            </select>
          </label>
          <button type="button" className="btn" onClick={exportCsv} title="Exporter les imprimantes affichées (CSV, ouvrable dans Excel)"><IconDownload width={15} height={15} />Exporter</button>
          {onAdd && <button type="button" className="btn btn-primary" onClick={onAdd}><IconPlus width={15} height={15} strokeWidth={2.2} />Ajouter une imprimante</button>}
        </div>
      </div>

      {!rows.length ? (
        <p className="muted" style={{ padding: '48px 20px', textAlign: 'center', margin: 0 }}>{filter === 'ALERT' && !needle ? 'Aucune imprimante en alerte : tous les niveaux suivis sont au-dessus des seuils.' : 'Aucune imprimante ne correspond.'}{filter && <> <button type="button" className="link" onClick={() => onFilter(null)}>Voir toutes les imprimantes</button></>}</p>
      ) : narrow ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: 12, background: 'var(--bg)' }}>
          {groups.map((g) => (
            <Fragment key={g.name || 'all'}>
              {g.name && <h3 className="faint" style={{ margin: '6px 2px 0', fontSize: 12, fontWeight: 600 }}>{g.name} · {g.items.length}</h3>}
              {g.items.map((p) => {
                const k = kindOf(p), issues = k === 'DOWN' ? [] : issuesOf(p)
                return (
                  <button key={p.id} type="button" className="pcard" onClick={() => onOpen(p.id)}>
                    <span style={{ display: 'flex', alignItems: 'flex-start', gap: 10, width: '100%' }}>
                      <span style={{ flex: 1, minWidth: 0 }}><span style={{ display: 'block', fontSize: 14.5, fontWeight: 600 }}>{pretty(p.name)}</span><span className="muted" style={{ display: 'block', fontSize: 12.5 }}>{placeOf(p)}</span></span>
                      <KindChip k={k} />
                    </span>
                    {k === 'DOWN' ? <span className="muted" style={{ fontSize: 12.5 }}>{downText(p, now)}</span> : <Inks p={p} />}
                    {issues.length > 0 && <span className="v-crit" style={{ fontSize: 12.5 }}>{issues.join(' · ')}</span>}
                  </button>
                )
              })}
            </Fragment>
          ))}
        </div>
      ) : (
        <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr>
              <th scope="col" style={{ width: 128 }}>État</th><th scope="col">Imprimante</th>
              <th scope="col">Encres <span className="faint" style={{ fontWeight: 400 }}>· % restant</span></th>
              {showBox && <th scope="col" title="Boîtier de maintenance (encre usagée) : pourcentage de remplissage">Maintenance</th>}
              <th scope="col">Rupture</th>
              {showIp && <th scope="col">Adresse IP</th>}<th scope="col">Dernier relevé</th>
            </tr></thead>
            <tbody>
              {groups.map((g) => (
                <Fragment key={g.name || 'all'}>
                  {g.name && <tr className="group"><td colSpan={cols}>{g.name} · {g.items.length}</td></tr>}
                  {g.items.map((p) => {
                    const k = kindOf(p), issues = k === 'DOWN' ? [] : issuesOf(p)
                    return (
                      <tr key={p.id} className={k === 'DOWN' ? 'is-down' : ''} onClick={() => onOpen(p.id)}>
                        <td><KindChip k={k} /></td>
                        <td style={{ whiteSpace: 'normal', minWidth: 220 }}>
                          <button type="button" className="row-link" onClick={(e) => { e.stopPropagation(); onOpen(p.id) }}>{pretty(p.name)}</button>
                          <div className="muted" style={{ fontSize: 12.5, marginTop: 1 }}>{[placeOf(p), p.user].filter(Boolean).join(' · ') || '—'}</div>
                          {issues.length > 0 && <div className="v-crit" style={{ fontSize: 12.5, marginTop: 2 }}>{issues.join(' · ')}</div>}
                        </td>
                        <td>{k === 'DOWN' ? <span className="muted" style={{ fontSize: 12.5 }}>{downText(p, now)}</span> : <Inks p={p} />}</td>
                        {showBox && <td>{k === 'DOWN' ? <span className="faint">—</span> : boxCell(p)}</td>}
                        <td>{etaCell(p, k)}</td>
                        {showIp && <td><span className="mono muted">{p.host}</span></td>}
                        <td className="muted" style={{ fontSize: 12.5 }} title={p.last_seen ? fmtDate(p.last_seen) : undefined}>{p.last_seen ? ago(p.last_seen, now) : 'jamais'}</td>
                      </tr>
                    )
                  })}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="faint" style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '12px 20px', fontSize: 12.5, borderTop: '1px solid var(--line)' }}>
        <span>{rows.length} imprimante{rows.length > 1 ? 's' : ''} sur {all.length}</span><span>Tri : urgence, puis rupture estimée</span>
      </div>
    </section>
  )
}
