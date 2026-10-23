import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { incidents as api, type Incident, type Printer } from './api'
import { IconOk, IconPlus, IconTool } from './Icons'
import { fmtDate } from './ui'

const sentence = (m: string) => m.charAt(0).toUpperCase() + m.slice(1) + (/[.!?]$/.test(m) ? '' : '.')
const who = (email: string | null) => (email ?? '').split('@')[0] || email || '—'

/** Onglet « Incidents » : déclarer un incident sur l'imprimante, le clôturer, et garder tout l'historique. */
export default function Incidents({ p, canWrite, onChanged }: { p: Printer; canWrite: boolean; onChanged: () => void }) {
  const [list, setList] = useState<Incident[] | null>(null)
  const [cats, setCats] = useState<string[]>([])
  const [err, setErr] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [cat, setCat] = useState('')
  const [desc, setDesc] = useState('')
  const [closing, setClosing] = useState<number | null>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try { setList(await api.list(p.id)) } catch (e) { setErr(sentence((e as Error).message)) }
  }, [p.id])
  useEffect(() => { void load() }, [load])
  useEffect(() => { if (canWrite) api.categories().then((c) => { setCats(c); setCat(c[0] ?? '') }).catch(() => {}) }, [canWrite])

  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setErr(null)
    try { await fn(); await load(); onChanged(); return true } catch (e) { setErr(sentence((e as Error).message)); return false } finally { setBusy(false) }
  }
  async function declare(e: FormEvent) {
    e.preventDefault()
    if (await act(() => api.add(p.id, cat, desc.trim()))) { setDesc(''); setAdding(false) }
  }
  async function resolve(e: FormEvent, id: number) {
    e.preventDefault()
    if (await act(() => api.resolve(id, note.trim()))) { setClosing(null); setNote('') }
  }

  const open = list?.filter((i) => i.resolved_at == null) ?? []
  const done = list?.filter((i) => i.resolved_at != null) ?? []

  return (
    <>
      <section aria-labelledby="inc-open" style={{ display: 'grid', gap: 12 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
          <h3 id="inc-open" className="h3" style={{ margin: 0 }}>En cours{open.length > 0 && <span className="faint" style={{ fontWeight: 400 }}> · {open.length}</span>}</h3>
          {canWrite && !adding && <button type="button" className="btn" onClick={() => setAdding(true)}><IconPlus width={15} height={15} />Déclarer un incident</button>}
        </div>

        {adding && (
          <form onSubmit={declare} className="note" style={{ flexDirection: 'column', gap: 12, background: 'var(--surface-2)', border: '1px solid var(--line)' }}>
            <label className="field">Type d'incident
              <select className="select" value={cat} onChange={(e) => setCat(e.target.value)} style={{ height: 38 }}>
                {cats.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </label>
            <label className="field">Description
              <textarea className="textarea" value={desc} onChange={(e) => setDesc(e.target.value)} maxLength={1000} rows={3} autoFocus required
                placeholder="Ce qui a été constaté, par qui il a été signalé, ce qui a déjà été tenté…" />
            </label>
            <div style={{ display: 'flex', gap: 8 }}>
              <button type="submit" className="btn btn-primary" disabled={busy || !cat || !desc.trim()}>{busy ? 'Enregistrement…' : 'Enregistrer'}</button>
              <button type="button" className="btn" onClick={() => { setAdding(false); setDesc('') }} disabled={busy}>Annuler</button>
            </div>
          </form>
        )}
        {err && <p role="alert" className="banner" data-k="crit" style={{ margin: 0 }}>{err}</p>}

        {!list ? <div className="skeleton" style={{ height: 80 }} /> : !open.length ? (
          <p className="muted" style={{ margin: 0, fontSize: 13.5 }}>Aucun incident en cours sur cette imprimante.</p>
        ) : open.map((i) => (
          <article key={i.id} className="incident" data-open="true">
            <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
              <IconTool width={17} height={17} style={{ flex: 'none', marginTop: 2, color: 'var(--crit)' }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600 }}>{i.category}</div>
                <p style={{ margin: '2px 0 0', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{i.description}</p>
                <p className="faint" style={{ margin: '6px 0 0', fontSize: 12.5 }}>Déclaré le {fmtDate(i.opened_at)} par {who(i.opened_by)}</p>
              </div>
              {canWrite && closing !== i.id && <button type="button" className="btn" style={{ height: 32 }} onClick={() => { setClosing(i.id); setNote('') }}>Clôturer</button>}
            </div>
            {closing === i.id && (
              <form onSubmit={(e) => void resolve(e, i.id)} style={{ display: 'grid', gap: 10, marginTop: 12 }}>
                <label className="field">Résolution (facultatif)
                  <textarea className="textarea" value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} rows={2} autoFocus placeholder="Intervention réalisée, pièce changée…" />
                </label>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? 'Enregistrement…' : 'Clôturer l\'incident'}</button>
                  <button type="button" className="btn" onClick={() => setClosing(null)} disabled={busy}>Annuler</button>
                </div>
              </form>
            )}
          </article>
        ))}
        {!canWrite && <p className="faint" style={{ margin: 0, fontSize: 12.5 }}>Votre compte est en lecture seule : la déclaration d'incidents demande le droit d'écriture.</p>}
      </section>

      <section aria-labelledby="inc-hist" style={{ display: 'grid', gap: 10 }}>
        <h3 id="inc-hist" className="h3" style={{ margin: 0 }}>Historique{done.length > 0 && <span className="faint" style={{ fontWeight: 400 }}> · {done.length}</span>}</h3>
        {list && !done.length && <p className="muted" style={{ margin: 0, fontSize: 13.5 }}>Aucun incident clôturé.</p>}
        {done.map((i) => (
          <article key={i.id} className="incident">
            <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
              <IconOk width={17} height={17} style={{ flex: 'none', marginTop: 2, color: 'var(--ok)' }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600 }}>{i.category}</div>
                <p style={{ margin: '2px 0 0', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{i.description}</p>
                {i.resolution && <p style={{ margin: '6px 0 0', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}><span className="muted">Résolution : </span>{i.resolution}</p>}
                <p className="faint" style={{ margin: '6px 0 0', fontSize: 12.5 }}>
                  Déclaré le {fmtDate(i.opened_at)} par {who(i.opened_by)} · clôturé le {fmtDate(i.resolved_at!)} par {who(i.resolved_by)}
                </p>
              </div>
            </div>
          </article>
        ))}
      </section>
    </>
  )
}
