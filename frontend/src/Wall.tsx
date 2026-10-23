import { useEffect, useMemo, useState } from 'react'
import type { Status } from './api'
import { type Kind, INK_LETTER, KIND_LABEL, issuesOf, kindOf, kpis, sortByUrgency, todos } from './derive'
import { Logo } from './Icons'
import { DAY, ago, isBox, pretty } from './ui'

const TONE: Record<string, string> = { crit: 'var(--crit)', warn: 'var(--warn)', down: 'var(--down)' }
const KIND_TONE: Record<Kind, string> = { CRIT: 'var(--crit)', INC: 'var(--crit)', WARN: 'var(--warn)', OK: 'var(--ok)', DOWN: 'var(--down)', NODATA: 'var(--down)' }

/** Écran mural de la salle informatique (`#/mur`) : lisible à distance, sans interaction, rafraîchi avec le reste de l'application. */
export default function Wall({ status, err }: { status: Status | null; err: string | null }) {
  const [clock, setClock] = useState(() => new Date())
  useEffect(() => { const t = setInterval(() => setClock(new Date()), 15_000); return () => clearInterval(t) }, [])
  useEffect(() => { document.documentElement.dataset.theme = 'dark'; return () => { delete document.documentElement.dataset.theme } }, [])
  const k = useMemo(() => (status ? kpis(status) : null), [status])
  const items = useMemo(() => (status ? todos(status).slice(0, 7) : []), [status])
  const tiles = useMemo(() => (status ? [...status.printers].sort(sortByUrgency) : []), [status])
  const now = status?.generated_at ?? Date.now() / 1000
  const last = status?.collector.last_cycle ?? null
  const cols = tiles.length > 24 ? 4 : 3

  return (
    <div className="theme-dark" style={{ minHeight: '100vh', boxSizing: 'border-box', background: 'var(--bg)', color: 'var(--text)', padding: '28px 40px', display: 'flex', flexDirection: 'column', gap: 24 }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 18 }}>
        <Logo size={34} />
        <span style={{ fontSize: 26, fontWeight: 600 }}>PrintWatch</span>
        <span style={{ fontSize: 21, color: 'var(--text-2)' }}>Parc d'impression</span>
        <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 10, fontSize: 19, color: err ? 'var(--crit)' : 'var(--text-2)' }}>
          <span className="dot" style={{ width: 12, height: 12, background: err ? 'var(--crit)' : status?.collector.state === 'ok' ? 'var(--ok)' : 'var(--warn)' }} />
          {err ? 'Connexion perdue' : last ? `Relevé ${ago(last, now)}` : 'Aucun relevé'}
        </span>
        <span style={{ fontSize: 40, fontWeight: 500, marginLeft: 24 }}>{clock.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}</span>
      </header>

      {!status || !k ? <div className="skeleton" style={{ flex: 1 }} /> : (
        <div style={{ flex: 1, minHeight: 0, display: 'grid', gridTemplateColumns: 'minmax(320px, 0.7fr) minmax(0, 1.1fr) minmax(0, 1.6fr)', gap: 24 }}>
          <div style={{ display: 'grid', gridTemplateRows: 'repeat(4, minmax(0, 1fr))', gap: 16 }}>
            {[
              ['Imprimantes en ligne', <>{k.online} <small style={{ fontSize: 30, color: 'var(--text-3)', fontWeight: 500 }}>/ {k.total}</small></>],
              ['Consommables à commander', <>{k.toOrder}{k.toOrderCrit > 0 && <small style={{ fontSize: 24, color: 'var(--crit)', fontWeight: 600 }}> dont {k.toOrderCrit} critique{k.toOrderCrit > 1 ? 's' : ''}</small>}</>],
              ['Incidents matériels', <>{k.incidents}</>],
              ['Sans réponse', <>{k.down}</>],
            ].map(([label, value], i) => (
              <div key={i} className="panel" style={{ padding: '20px 26px', display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 6, borderRadius: 10 }}>
                <span style={{ fontSize: 19, color: 'var(--text-2)' }}>{label}</span>
                <span style={{ fontSize: 60, fontWeight: 600, lineHeight: 1 }}>{value}</span>
              </div>
            ))}
          </div>

          <section className="panel" aria-label="À traiter" style={{ padding: '22px 28px', borderRadius: 10, minWidth: 0, overflow: 'hidden' }}>
            <h2 style={{ margin: '0 0 8px', fontSize: 25, fontWeight: 600 }}>À traiter</h2>
            {!items.length && <p style={{ fontSize: 21, color: 'var(--text-2)' }}>Rien à signaler.</p>}
            {items.map((t) => (
              <div key={t.key} style={{ display: 'grid', gridTemplateColumns: '8px minmax(0, 1fr)', gap: 20, alignItems: 'center', padding: '14px 0', borderTop: '1px solid var(--line)' }}>
                <span style={{ width: 8, height: 50, borderRadius: 4, background: TONE[t.tone] }} />
                <span style={{ minWidth: 0 }}><span style={{ display: 'block', fontSize: 22, fontWeight: 600 }}>{t.title}</span>
                  <span style={{ display: 'block', fontSize: 18, color: 'var(--text-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.sub}</span></span>
              </div>
            ))}
          </section>

          <section className="panel" aria-label="Parc" style={{ padding: '22px 24px', borderRadius: 10, display: 'flex', flexDirection: 'column', gap: 14, minWidth: 0 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}><h2 style={{ margin: 0, fontSize: 25, fontWeight: 600 }}>Parc</h2><span style={{ fontSize: 16, color: 'var(--text-2)' }}>Encre la plus basse</span></div>
            <div style={{ flex: 1, display: 'grid', gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`, gridAutoRows: 'minmax(0, 1fr)', gap: 10 }}>
              {tiles.map((p) => {
                const kd = kindOf(p)
                const low = p.supplies.filter((s) => !isBox(s) && !s.stale && s.pct != null).sort((a, b) => a.pct! - b.pct!)[0]
                const issue = issuesOf(p)[0]
                const sub = kd === 'DOWN' ? (p.last_seen ? `Sans réponse ${Math.max(1, Math.round((now - p.last_seen) / (now - p.last_seen < DAY ? 3600 : DAY)))} ${now - p.last_seen < DAY ? 'h' : 'j'}` : 'Jamais relevée') : issue ?? KIND_LABEL[kd]
                return (
                  <div key={p.id} style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 6, padding: '10px 14px', borderRadius: 8, minWidth: 0,
                    background: kd === 'DOWN' ? 'var(--bg)' : 'var(--surface-2)', border: `1px solid ${kd === 'CRIT' || kd === 'INC' ? 'color-mix(in srgb, var(--crit) 45%, var(--line))' : kd === 'WARN' ? 'color-mix(in srgb, var(--warn) 45%, var(--line))' : 'var(--line)'}` }}>
                    {/* Le nom a toute la largeur (deux lignes au plus) : sur un vrai parc, plusieurs noms commencent pareil. */}
                    <span title={pretty(p.name)} style={{ fontSize: 16, fontWeight: 600, lineHeight: 1.25, overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflowWrap: 'anywhere' }}>{pretty(p.name)}</span>
                    <span style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'baseline' }}>
                      <span style={{ fontSize: 14, fontWeight: 600, color: KIND_TONE[kd], overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{sub}</span>
                      <span style={{ fontSize: 15, fontWeight: 600, whiteSpace: 'nowrap', color: low && low.alert !== 'OK' ? (low.alert === 'CRIT' ? 'var(--crit)' : 'var(--warn)') : 'var(--text-2)' }}>{kd !== 'DOWN' && low ? `${INK_LETTER[low.color ?? ''] ?? ''} ${Math.round(low.pct!)} %` : '—'}</span>
                    </span>
                  </div>
                )
              })}
            </div>
          </section>
        </div>
      )}
    </div>
  )
}
