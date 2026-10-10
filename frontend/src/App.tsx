import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, write, type Collector, type Ev, type State, type Status } from './api'
import AddPanel from './AddPanel'
import Drawer from './Drawer'
import FleetTable from './FleetTable'
import { IconCrit, IconOk, IconSync, IconWarn, Logo } from './Icons'
import { ago, fmtDate, useRoute } from './ui'

const REFRESH_MS = 60_000
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

function usePoll() {
  const [data, setData] = useState<{ st: Status; ev: Ev[] } | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const load = useCallback(async (): Promise<Status | null> => {
    try {
      const [st, ev] = await Promise.all([api.status(), api.events(100)])
      setData({ st, ev }); setErr(null)
      return st
    } catch (e) { setErr((e as Error).message); return null }       // on garde les dernières données affichées
  }, [])
  useEffect(() => { void load(); const id = setInterval(load, REFRESH_MS); return () => clearInterval(id) }, [load])
  return { data, err, reload: load }
}

type SyncState = 'idle' | 'running' | 'done' | 'slow' | 'error'

/** Synchronisation à la demande : réveille la collecte, puis attend la fin du cycle en sondant l'API (90 s maximum). */
function useSync(reload: () => Promise<Status | null>, lastCycle: number | null) {
  const [state, setState] = useState<SyncState>('idle')
  const busy = useRef(false)
  const run = useCallback(async () => {
    if (busy.current) return
    busy.current = true; setState('running')
    const before = lastCycle ?? 0
    try {
      const { state: s } = await write.sync()
      const t0 = Date.now()
      for (;;) {
        await sleep(1500)
        const st = await reload()
        if (s === 'too_soon' || (st && !st.collector.running && (st.collector.last_cycle ?? 0) > before)) break
        if (Date.now() - t0 > 90_000) { setState('slow'); busy.current = false; return }
      }
      setState('done')
    } catch { setState('error') }
    busy.current = false
    setTimeout(() => setState('idle'), 3500)
  }, [reload, lastCycle])
  return { state, run }
}

function CollectorBanner({ col, now }: { col: Collector; now: number }) {
  const stopped = col.state === 'stale' || col.state === 'never'
  const Icon = stopped ? IconCrit : IconWarn
  const color = stopped ? 'var(--crit)' : 'var(--warn)'
  return (
    <p role="alert" className="flex items-start gap-3 rounded-xl px-4 py-3 text-sm" style={{ background: `color-mix(in srgb, ${color} 12%, transparent)`, color }}>
      <Icon width={18} height={18} className="mt-0.5 shrink-0" />
      <span>
        {col.state === 'never' && <><b>Aucune collecte enregistrée.</b> Les niveaux affichés ne proviennent d'aucun relevé SNMP. Lancez le collecteur : <code>printwatch serve --collect</code> ou <code>printwatch loop</code>.</>}
        {col.state === 'stale' && col.last_cycle != null && <><b>La collecte est à l'arrêt :</b> dernier relevé {ago(col.last_cycle, now)} (un cycle toutes les {Math.round(col.interval_s / 60)} min). Les niveaux affichés peuvent ne plus être à jour. Vérifiez que le collecteur tourne.</>}
        {col.state === 'all_failed' && <><b>Aucune imprimante n'a répondu au dernier relevé</b> ({col.failed} sur {col.polled} en échec). Vérifiez le réseau, la communauté ou les identifiants SNMP.</>}
      </span>
    </p>
  )
}

const SYNC_LABEL: Record<SyncState, string> = { idle: 'Synchroniser', running: 'Synchronisation…', done: 'À jour', slow: 'Toujours en cours…', error: 'Échec, réessayer' }

export default function App() {
  const { data, err, reload } = usePoll()
  const route = useRoute()
  const [filter, setFilter] = useState<State | null>(null)
  const [adding, setAdding] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  const s = data?.st.summary
  const col = data?.st.collector
  const caps = data?.st.capabilities
  const last = col?.last_cycle ?? null
  const sync = useSync(reload, last)
  useEffect(() => {
    document.title = s && (s.crit || s.warn) ? `(${s.crit + s.warn}) PrintWatch · Suivi des consommables` : 'PrintWatch · Suivi des consommables'
  }, [s])
  useEffect(() => { if (!notice) return; const t = setTimeout(() => setNotice(null), 7000); return () => clearTimeout(t) }, [notice])

  const open = useCallback((printer: string) => route.go(printer), [route])
  const close = useCallback(() => route.go(), [route])
  const printer = useMemo(() => data?.st.printers.find((p) => p.id === route.printer) ?? null, [data, route.printer])
  const canManage = !!caps && caps.manage !== 'off'

  const changed = useCallback(async (what: 'edited' | 'removed') => {
    if (what === 'removed') route.go()
    setNotice(what === 'removed' ? "Imprimante retirée de la supervision (son historique est conservé)." : 'Modifications enregistrées.')
    await reload()
  }, [reload, route])
  const added = useCallback(async (restored: boolean) => {
    setAdding(false)
    setNotice(restored ? 'Imprimante remise en supervision avec son historique.' : caps?.sync
      ? 'Imprimante ajoutée : premier relevé en cours. Sans réponse SNMP, elle apparaît sous « sans données », en bas de page.'
      : 'Imprimante ajoutée : elle apparaîtra au prochain cycle de collecte.')
    await reload()
  }, [reload, caps?.sync])

  return (
    <div className="min-h-screen">
      <a href="#contenu" className="skip" onClick={(e) => { e.preventDefault(); document.getElementById('contenu')?.focus() }}>Aller au contenu</a>
      <header className="topbar">
        <div className="mx-auto flex max-w-[1400px] items-center gap-x-4 px-4 py-2.5 sm:px-6">
          <a href="#/" className="flex items-center gap-3" aria-label="PrintWatch">
            <Logo size={34} />
            <span className="text-[19px] font-semibold leading-tight tracking-tight">PrintWatch</span>
          </a>
          <button type="button" onClick={() => void reload()} className="muted ml-auto flex items-center gap-2 rounded-full px-2 py-1 text-[13px] hover:bg-[var(--surface-2)]" title={last ? `Dernier relevé SNMP : ${fmtDate(last)}. Cliquer pour actualiser l'affichage.` : 'Cliquer pour actualiser'} aria-live="polite">
            <i className={`live-dot ${err || col?.state === 'stale' || col?.state === 'never' ? 'err' : col?.state === 'all_failed' ? 'warn' : ''}`} />
            {err ? 'Connexion perdue' : !data ? 'Chargement…' : last ? `Dernier relevé ${ago(last, data.st.generated_at)}` : 'Aucun relevé'}
          </button>
          {caps?.sync && (
            <button type="button" className="btn" onClick={() => void sync.run()} disabled={sync.state === 'running'} aria-live="polite"
              title="Interroge toutes les imprimantes maintenant, sans attendre le prochain cycle">
              {sync.state === 'done' ? <IconOk width={15} height={15} style={{ color: 'var(--ok)' }} /> : <IconSync width={15} height={15} className={sync.state === 'running' ? 'spin' : ''} />}
              {SYNC_LABEL[sync.state]}
            </button>
          )}
        </div>
      </header>

      <main id="contenu" tabIndex={-1} className="mx-auto max-w-[1400px] space-y-4 px-4 py-5 outline-none sm:px-6 sm:py-6">
        {err && (
          <p role="alert" className="rounded-xl px-4 py-3 text-sm" style={{ background: 'color-mix(in srgb, var(--crit) 12%, transparent)', color: 'var(--crit)' }}>
            {data ? `Données non actualisées (${err}). Les dernières valeurs restent affichées.` : `Aucune donnée : ${err}. Lancez un cycle de collecte avec « printwatch once ».`}
          </p>
        )}
        {data && col && col.state !== 'ok' && <CollectorBanner col={col} now={data.st.generated_at} />}
        {notice && <p role="status" className="flex items-center gap-2 rounded-xl px-4 py-3 text-sm" style={{ background: 'color-mix(in srgb, var(--ok) 12%, transparent)', color: 'var(--ok)' }}><IconOk width={16} height={16} />{notice}</p>}
        {!data && !err && <div className="grid gap-5 lg:grid-cols-2"><div className="skeleton h-64" /><div className="skeleton h-64" /></div>}
        {data && <FleetTable status={data.st} filter={filter} onFilter={setFilter} onOpen={open} onAdd={canManage ? () => setAdding(true) : undefined} />}
      </main>

      {data && printer && <Drawer key={printer.id} printer={printer} status={data.st} events={data.ev} onClose={close} onChanged={changed} />}
      {adding && caps && <AddPanel manage={caps.manage} canSync={caps.sync} onClose={() => setAdding(false)} onAdded={added} />}
    </div>
  )
}
