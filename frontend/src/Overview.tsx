import { useMemo } from 'react'
import type { Status } from './api'
import { DEFAULT_FILTER, type Filter, type Kind, fmtDays, kpis } from './derive'
import { IconClock, IconCrit, IconTool } from './Icons'

interface Props { status: Status; filter: Filter | null; onFilter: (k: Filter | null) => void; onOpen: (id: string) => void }

/** Indicateurs clés : chacun filtre le tableau du parc (un second clic retire le filtre). */
export function Kpis({ status, filter, onFilter }: Omit<Props, 'onOpen'>) {
  const k = useMemo(() => kpis(status), [status])
  const toggle = (f: Kind) => onFilter(filter === f ? DEFAULT_FILTER : f)
  const onlinePct = k.total ? Math.round((k.online / k.total) * 100) : 0
  return (
    <section aria-label="Indicateurs clés" className="kpis">
      <button type="button" className="kpi" aria-pressed={filter === 'DOWN'} onClick={() => toggle('DOWN')} title="Afficher les imprimantes sans réponse">
        <span className="kpi-l">Imprimantes en ligne</span>
        <span className="kpi-v">{k.online} <small>/ {k.total}</small></span>
        <span className="track thin" aria-hidden="true"><i style={{ width: `${onlinePct}%`, background: 'var(--accent)' }} /></span>
        <span className="kpi-s">{k.down ? `${k.down} sans réponse` : 'Toutes répondent'}</span>
      </button>
      <button type="button" className="kpi" aria-pressed={filter === 'CRIT'} onClick={() => toggle('CRIT')} title="Afficher les imprimantes en état critique">
        <span className="kpi-l"><IconCrit width={15} height={15} style={{ color: k.toOrderCrit ? 'var(--crit)' : 'var(--text-3)' }} />Consommables à commander</span>
        <span className="kpi-v">{k.toOrder}</span>
        <span className="kpi-s">{k.toOrderCrit ? <><b className="v-crit">{k.toOrderCrit} critique{k.toOrderCrit > 1 ? 's' : ''}</b> · {k.toOrder - k.toOrderCrit} sous le seuil d'attention</> : k.toOrder ? 'Sous le seuil d’attention' : 'Aucun consommable bas'}</span>
      </button>
      <div className="kpi">
        <span className="kpi-l"><IconClock width={15} height={15} style={{ color: k.soon ? 'var(--warn)' : 'var(--text-3)' }} />Ruptures sous 14 jours</span>
        <span className="kpi-v">{k.soon}</span>
        <span className="kpi-s">{k.next ? `Prochaine : ${k.next.label}, ${fmtDays(k.next.days)}` : 'Aucune rupture prévue'}</span>
      </div>
      <button type="button" className="kpi" aria-pressed={filter === 'INC'} onClick={() => toggle('INC')} title="Afficher les imprimantes avec un incident matériel">
        <span className="kpi-l"><IconTool width={15} height={15} style={{ color: k.incidents ? 'var(--crit)' : 'var(--text-3)' }} />Incidents matériels</span>
        <span className="kpi-v">{k.incidents}</span>
        <span className="kpi-s">{k.incidentText || 'Aucun incident signalé'}</span>
      </button>
    </section>
  )
}
