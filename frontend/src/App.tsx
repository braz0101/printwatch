import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, write, type Collector, type Ev, type Me, type Status } from './api'
import Accounts from './Accounts'
import AddPanel from './AddPanel'
import { DEFAULT_FILTER, type Filter, kpis } from './derive'
import Drawer from './Drawer'
import FleetTable from './FleetTable'
import { IconCrit, IconLogout, IconOk, IconScreen, IconSearch, IconSliders, IconSync, IconUser, IconWarn, Logo } from './Icons'
import { Kpis } from './Overview'
import Settings from './Settings'
import Tickets from './Tickets'
import { ago, fmtDate, useRoute } from './ui'
import Wall from './Wall'

const REFRESH_MS = 60_000
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

function usePoll() {
  const [data, setData] = useState<{ st: Status; ev: Ev[] } | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const load = useCallback(async (): Promise<Status | null> => {
    try {
      const [st, ev] = await Promise.all([api.status(), api.events(200)])
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
  return (
    <div role="alert" className="banner" data-k={stopped ? 'crit' : 'warn'}>
      {stopped ? <IconCrit width={18} height={18} style={{ flex: 'none', marginTop: 1 }} /> : <IconWarn width={18} height={18} style={{ flex: 'none', marginTop: 1 }} />}
      <span>
        {col.state === 'never' && <><b>Aucune collecte enregistrée.</b> Les niveaux affichés ne proviennent d'aucun relevé SNMP : vérifiez que le service PrintWatch tourne.</>}
        {col.state === 'stale' && col.last_cycle != null && <><b>La collecte est à l'arrêt :</b> dernier relevé {ago(col.last_cycle, now)} (un cycle toutes les {Math.round(col.interval_s / 60)} min). Les valeurs affichées peuvent ne plus être à jour.</>}
        {col.state === 'all_failed' && <><b>Aucune imprimante n'a répondu au dernier relevé</b> ({col.failed} sur {col.polled} en échec). Vérifiez le réseau ou la communauté SNMP.</>}
      </span>
    </div>
  )
}

/** « · noir et cyan suivis » quand une partie seulement des consommables déclenche des alertes. */
const INK_NAME: Record<string, string> = { black: 'noir', cyan: 'cyan', magenta: 'magenta', yellow: 'jaune', box: 'boîtier', other: 'autres' }
function trackedText(inks?: string[]) {
  if (!inks || inks.length >= 6) return ''
  const n = inks.map((i) => INK_NAME[i] ?? i)
  return ` · alertes : ${n.length > 1 ? `${n.slice(0, -1).join(', ')} et ${n[n.length - 1]}` : n[0]}`
}

export const ROLE_LABEL: Record<string, string> = { lecture: 'Lecture', ecriture: 'Écriture', admin: 'Admin' }

const SYNC_LABEL: Record<SyncState, string> = { idle: 'Synchroniser', running: 'Synchronisation…', done: 'À jour', slow: 'Toujours en cours…', error: 'Échec, réessayer' }

interface Props { me: Me; onMe: (m: Me) => void; onLogout: () => void }

export default function App({ me, onMe, onLogout }: Props) {
  const { data, err, reload } = usePoll()
  const route = useRoute()
  const accounts = me.auth && route.path.startsWith('/comptes')
  const params = route.path.startsWith('/parametres')
  const page = accounts || params || route.path.startsWith('/tickets')
  const [filter, setFilter] = useState<Filter | null>(DEFAULT_FILTER)
  const [query, setQuery] = useState('')
  const [adding, setAdding] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const fleetRef = useRef<HTMLDivElement>(null)

  const st = data?.st
  const col = st?.collector
  const caps = st?.capabilities
  // RÉSERVÉ AU RÔLE « écriture » : la liste porte les intitulés des demandes de tout le
  // personnel, parfois un nom ou un message d'erreur. Le rôle « lecture » existe pour
  // regarder des niveaux d'encre.
  //
  // LE RÔLE VIENT DU SERVEUR (`capabilities.role`), pas du compte mémorisé : c'est lui
  // qui tranche, et il revérifie chaque appel. `me.role` n'est qu'un repli le temps de la
  // première réponse. D'où la place de ces deux lignes, APRÈS `caps` — les poser plus
  // haut lisait une variable qui n'existait pas encore.
  const peutRepartir = (caps?.role ?? me.role) !== 'lecture'
  const billets = me.auth && peutRepartir && route.path.startsWith('/tickets')
  const last = col?.last_cycle ?? null
  const sync = useSync(reload, last)
  const k = useMemo(() => (st ? kpis(st) : null), [st])
  useEffect(() => {
    const n = k ? k.counts.CRIT + k.counts.INC : 0
    document.title = n ? `(${n}) PrintWatch · Parc d'impression` : "PrintWatch · Parc d'impression"
  }, [k])
  useEffect(() => { if (!notice) return; const t = setTimeout(() => setNotice(null), 7000); return () => clearTimeout(t) }, [notice])

  const open = useCallback((printer: string) => route.go(printer), [route])
  const close = useCallback(() => route.go(), [route])
  const printer = useMemo(() => st?.printers.find((p) => p.id === route.printer) ?? null, [st, route.printer])
  const canManage = !!caps && caps.manage !== 'off'
  /** Droits frais à chaque actualisation (un changement de rôle s'applique sans reconnexion) ; sans comptes (démo) : tout. */
  const isAdmin = !me.auth || (caps?.admin ?? me.role === 'admin')
  const role = caps?.role ?? me.role
  /** Un filtre posé depuis les indicateurs ou « À traiter » amène le tableau à l'écran. */
  const filterAndShow = useCallback((f: Filter | null) => {
    setFilter(f)
    if (f) requestAnimationFrame(() => fleetRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
  }, [])

  const changed = useCallback(async (what: 'edited' | 'removed' | 'incident') => {
    if (what === 'removed') route.go()
    if (what !== 'incident') setNotice(what === 'removed' ? 'Imprimante retirée de la supervision (son historique est conservé).' : 'Modifications enregistrées.')
    await reload()
  }, [reload, route])
  const added = useCallback(async (restored: boolean) => {
    setAdding(false)
    setNotice(restored ? 'Imprimante remise en supervision avec son historique.' : caps?.sync
      ? 'Imprimante ajoutée : premier relevé en cours.' : 'Imprimante ajoutée : elle sera relevée au prochain cycle de collecte.')
    await reload()
  }, [reload, caps?.sync])

  if (route.path.startsWith('/mur')) return <Wall status={st ?? null} err={err} />

  const liveColor = err || col?.state === 'stale' || col?.state === 'never' ? 'var(--crit)' : col?.state === 'all_failed' ? 'var(--warn-bar)' : 'var(--ok)'

  return (
    <div style={{ minHeight: '100vh' }}>
      <a href="#contenu" className="skip" onClick={(e) => { e.preventDefault(); document.getElementById('contenu')?.focus() }}>Aller au contenu</a>
      <header className="topbar">
        <div className="container" style={{ minHeight: 60, display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '10px 20px', paddingTop: 10, paddingBottom: 10, boxSizing: 'border-box' }}>
          <a href="#/" onClick={(e) => { e.preventDefault(); route.go(); setFilter(DEFAULT_FILTER); setQuery('') }} style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--text)', textDecoration: 'none' }}>
            <Logo size={26} /><span style={{ fontSize: 16, fontWeight: 600 }}>PrintWatch</span>
          </a>
          <span aria-hidden="true" style={{ width: 1, height: 20, background: 'var(--line)' }} />
          <span className="muted" style={{ fontSize: 13.5 }}>Parc d'impression</span>
          <label style={{ flex: '1 1 300px', maxWidth: 460, marginLeft: 'auto', position: 'relative', display: 'block' }}>
            <span className="sr-only">Rechercher une imprimante</span>
            <IconSearch width={16} height={16} style={{ position: 'absolute', left: 12, top: 10, color: 'var(--text-3)' }} />
            <input type="search" className="input" value={query} onChange={(e) => { setQuery(e.target.value); if (e.target.value && filter === DEFAULT_FILTER) setFilter(null); if (page) route.go(); else if (e.target.value) fleetRef.current?.scrollIntoView({ block: 'start' }) }}
              placeholder="Imprimante, adresse IP, n° de série, utilisateur…" style={{ width: '100%', boxSizing: 'border-box', paddingLeft: 36 }} />
          </label>
          <button type="button" onClick={() => void reload()} className="seg" style={{ fontWeight: 400 }} aria-live="polite"
            title={last ? `Dernier cycle de collecte : ${fmtDate(last)}. Cliquer pour actualiser l'affichage.` : "Cliquer pour actualiser l'affichage"}>
            <span className="dot" style={{ background: liveColor }} />
            {err ? 'Connexion perdue' : !data ? 'Chargement…' : last ? `Relevé ${ago(last, st!.generated_at)}` : 'Aucun relevé'}
          </button>
          {caps?.sync && (
            <button type="button" className="btn" onClick={() => void sync.run()} disabled={sync.state === 'running'} aria-live="polite" title="Interroger toutes les imprimantes maintenant">
              {sync.state === 'done' ? <IconOk width={15} height={15} style={{ color: 'var(--ok)' }} /> : <IconSync width={15} height={15} className={sync.state === 'running' ? 'spin' : ''} />}
              {SYNC_LABEL[sync.state]}
            </button>
          )}
          {me.auth && peutRepartir && <a href="#/tickets" className="btn" title="Répartir les tickets d'assistance (GLPI)"
            aria-current={billets ? 'page' : undefined} style={billets ? { background: 'var(--hover)' } : undefined}>Tickets</a>}
          <a href="#/mur" className="btn btn-icon" title="Écran mural (salle informatique)" aria-label="Ouvrir l'écran mural"><IconScreen width={16} height={16} /></a>
          {isAdmin && <a href="#/parametres" className="btn btn-icon" title="Paramètres : seuils d'alerte et encres suivies" aria-label="Paramètres"
            aria-current={params ? 'page' : undefined} style={params ? { background: 'var(--hover)' } : undefined}><IconSliders width={16} height={16} /></a>}
          {me.auth && <>
            <a href="#/comptes" className="user-chip" aria-current={accounts ? 'page' : undefined} title={isAdmin ? 'Mon mot de passe et gestion des comptes' : 'Mon mot de passe'}>
              <IconUser width={16} height={16} style={{ flex: 'none' }} /><span>{me.email}</span>
              {role && <span className="role-tag" data-role={role}>{ROLE_LABEL[role]}</span>}
            </a>
            <button type="button" className="btn btn-icon" onClick={onLogout} title="Se déconnecter" aria-label="Se déconnecter"><IconLogout width={16} height={16} /></button>
          </>}
        </div>
      </header>

      <main id="contenu" tabIndex={-1} className="container" style={{ paddingTop: 28, paddingBottom: 48, display: 'flex', flexDirection: 'column', gap: 24, outline: 'none' }}>
        {me.must_change && <div role="alert" className="banner" data-k="warn"><IconWarn width={18} height={18} style={{ flex: 'none', marginTop: 1 }} />
          <span><b>Votre mot de passe est provisoire.</b> {accounts ? 'Remplacez-le ci-dessous par un mot de passe personnel.' : <>Remplacez-le par un mot de passe personnel : <a href="#/comptes" className="link">changer mon mot de passe</a>.</>}</span></div>}
        {accounts ? <Accounts me={me} onMe={onMe} isAdmin={isAdmin} /> : billets ? <Tickets isAdmin={isAdmin} /> : params ? <Settings onSaved={() => void reload()} readOnly={!isAdmin} /> : <>
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-end', justifyContent: 'space-between', gap: '6px 24px' }}>
          <div>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600, letterSpacing: '-.01em' }}>Vue d'ensemble</h1>
            <p className="muted" style={{ margin: '4px 0 0', fontSize: 13.5 }}>
              {st ? `${st.printers.length} imprimantes supervisées · ${st.collector.interval_s ? `relevé SNMP toutes les ${Math.round(st.collector.interval_s / 60)} min` : 'relevé SNMP à la demande (bouton Synchroniser)'}` : 'Chargement du parc…'}
            </p>
          </div>
          {st && <p className="faint" style={{ margin: 0, fontSize: 12.5 }}>Seuils : attention sous {st.thresholds.warn} %, critique sous {st.thresholds.crit} %{trackedText(st.thresholds.inks)} · <a href="#/parametres" className="link" style={{ fontSize: 12.5 }}>modifier</a></p>}
        </div>

        {err && <div role="alert" className="banner" data-k="crit"><IconCrit width={18} height={18} style={{ flex: 'none', marginTop: 1 }} />
          <span>{data ? `Données non actualisées (${err}). Les dernières valeurs restent affichées.` : `Impossible de charger les données : ${err}.`}</span></div>}
        {st && col && col.state !== 'ok' && <CollectorBanner col={col} now={st.generated_at} />}
        {notice && <div role="status" className="banner" data-k="ok"><IconOk width={18} height={18} style={{ flex: 'none' }} />{notice}</div>}

        {!st ? (
          <>{!err && <><div className="kpis">{[0, 1, 2, 3, 4].map((i) => <div key={i} className="skeleton" style={{ height: 118 }} />)}</div><div className="skeleton" style={{ height: 380 }} /><div className="skeleton" style={{ height: 520 }} /></>}</>
        ) : <>
          <Kpis status={st} filter={filter} onFilter={filterAndShow} />
          <div ref={fleetRef} style={{ scrollMarginTop: 76 }}>
            <FleetTable status={st} query={query} filter={filter} onFilter={setFilter} onOpen={open} onAdd={canManage ? () => setAdding(true) : undefined} />
          </div>
        </>}
        </>}
      </main>

      {st && printer && <Drawer key={printer.id} printer={printer} status={st} events={data!.ev} onClose={close} onChanged={changed} />}
      {adding && caps && <AddPanel manage={caps.manage} canSync={caps.sync} onClose={() => setAdding(false)} onAdded={added} />}
    </div>
  )
}
