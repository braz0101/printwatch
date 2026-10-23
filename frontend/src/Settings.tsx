import { useEffect, useState, type FormEvent } from 'react'
import { settings as api, type InkGroup, type Settings as S } from './api'
import { IconCrit, IconOk } from './Icons'

const GROUPS: { id: InkGroup; label: string; color: string }[] = [
  { id: 'black', label: 'Noir', color: 'var(--ink-black)' },
  { id: 'cyan', label: 'Cyan', color: 'var(--ink-cyan)' },
  { id: 'magenta', label: 'Magenta', color: 'var(--ink-magenta)' },
  { id: 'yellow', label: 'Jaune', color: 'var(--ink-yellow)' },
  { id: 'box', label: 'Boîtier de maintenance', color: 'var(--down)' },
  { id: 'other', label: 'Autres consommables', color: 'var(--ink-other)' },
]
const sentence = (m: string) => m.charAt(0).toUpperCase() + m.slice(1) + (/[.!?]$/.test(m) ? '' : '.')

/** Réglages des alertes : seuils et consommables suivis. Effet immédiat à l'écran ; courriels à la prochaine synchronisation. */
export default function Settings({ onSaved, readOnly = false }: { onSaved: () => void; readOnly?: boolean }) {
  const [cur, setCur] = useState<S | null>(null)
  const [warn, setWarn] = useState('')
  const [crit, setCrit] = useState('')
  const [inks, setInks] = useState<InkGroup[]>([])
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const load = (s: S) => { setCur(s); setWarn(String(s.warn)); setCrit(String(s.crit)); setInks(s.inks) }
  useEffect(() => { api.get().then(load).catch((e) => setMsg({ ok: false, text: sentence((e as Error).message) })) }, [])

  const w = Number(warn), c = Number(crit)
  const valid = warn !== '' && crit !== '' && c >= 0 && w <= 100 && c < w
  const dirty = !!cur && (w !== cur.warn || c !== cur.crit || inks.length !== cur.inks.length || inks.some((i) => !cur.inks.includes(i)))
  const toggle = (id: InkGroup) => setInks((v) => (v.includes(id) ? v.filter((x) => x !== id) : [...v, id]))

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true); setMsg(null)
    try {
      load(await api.save(w, c, GROUPS.map((g) => g.id).filter((id) => inks.includes(id))))
      setMsg({ ok: true, text: 'Réglages enregistrés. Le tableau de bord est à jour ; les alertes par courriel suivront à la prochaine synchronisation.' })
      onSaved()
    } catch (x) { setMsg({ ok: false, text: sentence((x as Error).message) }) }
    setBusy(false)
  }

  return (
    <>
      <div>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600, letterSpacing: '-.01em' }}>Paramètres</h1>
        <p className="muted" style={{ margin: '4px 0 0', fontSize: 13.5 }}>Seuils d'alerte et consommables à surveiller</p>
      </div>
      {!cur ? (msg ? <p role="alert" className="banner" data-k="crit" style={{ margin: 0 }}>{msg.text}</p> : <div className="skeleton" style={{ height: 360 }} />) : (
        <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
          {readOnly && <p className="note" style={{ margin: 0 }}>Consultation seulement : la modification des réglages est réservée aux administrateurs.</p>}
          <fieldset disabled={readOnly} style={{ border: 0, margin: 0, padding: 0, minWidth: 0 }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 24, alignItems: 'stretch' }}>
            <section className="panel" aria-labelledby="th-title" style={{ flex: '1 1 360px' }}>
              <div className="panel-head" style={{ borderBottom: '1px solid var(--line-soft)' }}>
                <div><h2 id="th-title" className="h2">Seuils d'alerte</h2><p className="sub">En pourcentage d'encre restante (boîtier de maintenance : place restante)</p></div>
              </div>
              <div style={{ padding: 20, display: 'grid', gap: 18 }}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                  <label className="field">Attention sous
                    <span className="pct-input"><input type="number" min={1} max={100} step={1} value={warn} onChange={(e) => setWarn(e.target.value)} required /><span>%</span></span>
                  </label>
                  <label className="field">Critique sous
                    <span className="pct-input"><input type="number" min={0} max={99} step={1} value={crit} onChange={(e) => setCrit(e.target.value)} required /><span>%</span></span>
                  </label>
                </div>
                {valid ? (
                  <div aria-hidden="true">
                    <div style={{ display: 'flex', height: 10, borderRadius: 5, overflow: 'hidden' }}>
                      <span style={{ width: `${c}%`, background: 'var(--crit)' }} />
                      <span style={{ width: `${w - c}%`, background: 'var(--warn-bar)' }} />
                      <span style={{ flex: 1, background: 'var(--ok)' }} />
                    </div>
                    <div className="faint" style={{ position: 'relative', height: 16, marginTop: 4, fontSize: 11.5 }}>
                      <span style={{ position: 'absolute', left: 0 }}>0</span>
                      {c > 6 && <span style={{ position: 'absolute', left: `${c}%`, transform: 'translateX(-50%)' }}>{c}</span>}
                      {w - c > 6 && w < 94 && <span style={{ position: 'absolute', left: `${w}%`, transform: 'translateX(-50%)' }}>{w}</span>}
                      <span style={{ position: 'absolute', right: 0 }}>100 %</span>
                    </div>
                  </div>
                ) : <p role="alert" className="v-crit" style={{ margin: 0, fontSize: 12.5, fontWeight: 400 }}>Le seuil critique doit être inférieur au seuil d'attention (entre 0 et 100 %).</p>}
                <p className="faint" style={{ margin: 0, fontSize: 12.5 }}>Critique : à commander tout de suite. Attention : à prévoir.</p>
              </div>
            </section>

            <section className="panel" aria-labelledby="ink-title" style={{ flex: '1 1 360px' }}>
              <div className="panel-head" style={{ borderBottom: '1px solid var(--line-soft)' }}>
                <div><h2 id="ink-title" className="h2">Consommables suivis</h2><p className="sub">Seuls ceux-ci déclenchent des alertes</p></div>
                <button type="button" className="link" style={{ fontSize: 13 }} onClick={() => setInks(GROUPS.map((g) => g.id))}>Tout cocher</button>
              </div>
              <fieldset style={{ border: 0, margin: 0, padding: 20, display: 'grid', gap: 12 }}>
                <legend className="sr-only">Consommables suivis</legend>
                {GROUPS.map((g) => (
                  <label key={g.id} className="check" style={{ alignItems: 'center' }}>
                    <input type="checkbox" checked={inks.includes(g.id)} onChange={() => toggle(g.id)} />
                    <span className="swatch" style={{ background: g.color }} />
                    <span>{g.label}</span>
                  </label>
                ))}
                <p className="faint" style={{ margin: '4px 0 0', fontSize: 12.5 }}>
                  {inks.length ? 'Les consommables décochés restent affichés, mais ne passent jamais en attention ni en critique et n\'envoient aucun courriel.' : <span className="v-crit" style={{ fontWeight: 400 }}>Cochez au moins un consommable.</span>}
                </p>
              </fieldset>
            </section>
          </div>

          </fieldset>
          {msg && (msg.ok
            ? <p role="status" className="banner" data-k="ok" style={{ margin: 0 }}><IconOk width={18} height={18} style={{ flex: 'none' }} />{msg.text}</p>
            : <p role="alert" className="banner" data-k="crit" style={{ margin: 0 }}><IconCrit width={18} height={18} style={{ flex: 'none', marginTop: 1 }} />{msg.text}</p>)}
          {!readOnly && <div style={{ display: 'flex', gap: 8 }}>
            <button type="submit" className="btn btn-primary" disabled={busy || !valid || !inks.length || !dirty}>{busy ? 'Enregistrement…' : 'Enregistrer'}</button>
            {dirty && <button type="button" className="btn" disabled={busy} onClick={() => load(cur)}>Annuler les modifications</button>}
          </div>}
        </form>
      )}
    </>
  )
}
