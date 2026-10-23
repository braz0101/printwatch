import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { auth, journal, type Account, type AuditEntry, type AuditFilter, type Me, type Role } from './api'
import { IconCrit, IconLock, IconOk, IconPlus } from './Icons'
import { fmtDate } from './ui'

const MIN = 10
const ROLES: { id: Role; label: string; hint: string }[] = [
  { id: 'lecture', label: 'Lecture', hint: 'consulter' },
  { id: 'ecriture', label: 'Écriture', hint: '+ synchroniser, incidents, imprimantes' },
  { id: 'admin', label: 'Admin', hint: '+ comptes et paramètres' },
]
const roleLabel = (r: Role) => ROLES.find((x) => x.id === r)?.label ?? r
const sentence = (m: string) => m.charAt(0).toUpperCase() + m.slice(1) + (/[.!?]$/.test(m) ? '' : '.')

function Feedback({ msg }: { msg: { ok: boolean; text: string } | null }) {
  if (!msg) return null
  return msg.ok
    ? <p role="status" className="banner" data-k="ok" style={{ margin: 0 }}><IconOk width={18} height={18} style={{ flex: 'none' }} />{msg.text}</p>
    : <p role="alert" className="banner" data-k="crit" style={{ margin: 0 }}><IconCrit width={18} height={18} style={{ flex: 'none', marginTop: 1 }} />{msg.text}</p>
}

/** Changer son propre mot de passe : l'actuel est exigé ; les autres sessions du compte sont fermées par le serveur. */
export function OwnPassword({ me, onMe }: { me: Me; onMe: (m: Me) => void }) {
  const [cur, setCur] = useState('')
  const [next, setNext] = useState('')
  const [again, setAgain] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const mismatch = again !== '' && again !== next

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true); setMsg(null)
    try {
      onMe(await auth.changePassword(cur, next))
      setCur(''); setNext(''); setAgain('')
      setMsg({ ok: true, text: 'Mot de passe changé. Vos autres sessions ouvertes ont été fermées.' })
    } catch (x) { setMsg({ ok: false, text: sentence((x as Error).message) }) }
    setBusy(false)
  }

  return (
    <section className="panel" aria-labelledby="pw-title" style={{ flex: '1 1 340px', maxWidth: 520 }}>
      <div className="panel-head" style={{ borderBottom: '1px solid var(--line-soft)' }}>
        <div><h2 id="pw-title" className="h2">Mon mot de passe</h2><p className="sub">{me.email}</p></div>
        <IconLock width={18} height={18} style={{ color: 'var(--text-3)' }} />
      </div>
      <form onSubmit={submit} className="grid gap-4" style={{ padding: 20 }}>
        <label className="field">Mot de passe actuel
          <input type="password" value={cur} onChange={(e) => setCur(e.target.value)} autoComplete="current-password" required />
        </label>
        <label className="field">Nouveau mot de passe
          <input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" minLength={MIN} required aria-describedby="pw-hint" />
        </label>
        <label className="field">Confirmer le nouveau mot de passe
          <input type="password" value={again} onChange={(e) => setAgain(e.target.value)} autoComplete="new-password" required aria-invalid={mismatch} />
        </label>
        <p id="pw-hint" className="faint" style={{ margin: 0, fontSize: 12.5 }}>
          {mismatch ? <span style={{ color: 'var(--crit-text)' }}>Les deux saisies diffèrent.</span> : `Au moins ${MIN} caractères, différent de votre adresse e-mail.`}
        </p>
        <Feedback msg={msg} />
        <div><button type="submit" className="btn btn-primary" disabled={busy || !cur || next.length < MIN || next !== again}>
          {busy ? 'Enregistrement…' : 'Changer le mot de passe'}</button></div>
      </form>
    </section>
  )
}

/** Liste des comptes : ajout (mot de passe provisoire), réinitialisation et retrait des autres comptes. */
function AccountList({ me }: { me: Me }) {
  const [list, setList] = useState<Account[] | null>(null)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [email, setEmail] = useState('')
  const [temp, setTemp] = useState('')
  const [newRole, setNewRole] = useState<Role>('ecriture')
  const [resetFor, setResetFor] = useState<string | null>(null)
  const [resetPw, setResetPw] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try { setList(await auth.users()) } catch (x) { setMsg({ ok: false, text: sentence((x as Error).message) }) }
  }, [])
  useEffect(() => { void load() }, [load])

  async function act(fn: () => Promise<unknown>, ok: string) {
    setBusy(true); setMsg(null)
    try { await fn(); setMsg({ ok: true, text: ok }); await load(); return true } catch (x) { setMsg({ ok: false, text: sentence((x as Error).message) }); return false } finally { setBusy(false) }
  }
  async function add(e: FormEvent) {
    e.preventDefault()
    const who = email.trim().toLowerCase()
    if (await act(() => auth.addUser(who, temp, newRole), `Compte ${who} créé (${roleLabel(newRole).toLowerCase()}). Communiquez-lui son mot de passe provisoire : il devra le changer à sa première connexion.`)) { setEmail(''); setTemp(''); setNewRole('ecriture') }
  }
  async function reset(e: FormEvent, who: string) {
    e.preventDefault()
    if (await act(() => auth.resetPassword(who, resetPw), `Mot de passe de ${who} réinitialisé. Ses sessions ouvertes ont été fermées.`)) { setResetFor(null); setResetPw('') }
  }
  function changeRole(who: string, role: Role) {
    void act(() => auth.setRole(who, role), `Droits de ${who} : ${roleLabel(role).toLowerCase()}. Effet immédiat, sans reconnexion.`)
  }
  function remove(who: string) {
    if (window.confirm(`Retirer le compte ${who} ? Cette personne n'aura plus accès à PrintWatch.`)) void act(() => auth.removeUser(who), `Compte ${who} retiré.`)
  }

  return (
    <section className="panel" aria-labelledby="acc-title" style={{ flex: '2 1 520px', minWidth: 0 }}>
      <div className="panel-head" style={{ borderBottom: '1px solid var(--line-soft)' }}>
        <div><h2 id="acc-title" className="h2">Comptes autorisés</h2><p className="sub">Seules ces adresses peuvent se connecter au tableau de bord.</p></div>
        {list && <span className="faint" style={{ fontSize: 12.5 }}>{list.length} compte{list.length > 1 ? 's' : ''}</span>}
      </div>
      {msg && <div style={{ padding: '16px 20px 0' }}><Feedback msg={msg} /></div>}
      <div className="tbl-wrap">
        <table className="tbl">
          <thead><tr><th>Compte</th><th>Droits</th><th>Mot de passe</th><th>Modifié le</th><th><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>
            {!list && <tr><td colSpan={5}><div className="skeleton" style={{ height: 120 }} /></td></tr>}
            {list?.map((u) => {
              const self = u.email === me.email
              return [
                <tr key={u.email} style={{ cursor: 'default' }}>
                  <td style={{ fontWeight: 500 }}>{u.email}{self && <span className="faint" style={{ fontWeight: 400 }}> · vous</span>}</td>
                  <td>{self ? <span className="role-tag" data-role={u.role}>{roleLabel(u.role)}</span> : (
                    <select className="select" style={{ height: 32 }} value={u.role} disabled={busy} aria-label={`Droits de ${u.email}`}
                      onChange={(e) => changeRole(u.email, e.target.value as Role)}>
                      {ROLES.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
                    </select>)}</td>
                  <td>{u.must_change ? <span className="chip" data-k="WARN">Provisoire</span> : <span className="chip" data-k="OK">Personnel</span>}</td>
                  <td className="muted">{u.updated_at ? fmtDate(u.updated_at) : '—'}</td>
                  <td style={{ textAlign: 'right' }}>
                    {!self && <div style={{ display: 'inline-flex', gap: 8 }}>
                      <button type="button" className="btn" style={{ height: 32 }} disabled={busy} onClick={() => { setResetFor(resetFor === u.email ? null : u.email); setResetPw('') }} aria-expanded={resetFor === u.email}>Réinitialiser</button>
                      <button type="button" className="btn btn-danger" style={{ height: 32 }} disabled={busy} onClick={() => remove(u.email)}>Retirer</button>
                    </div>}
                  </td>
                </tr>,
                resetFor === u.email && (
                  <tr key={u.email + '-reset'} style={{ cursor: 'default' }}><td colSpan={5} style={{ background: 'var(--surface-2)', whiteSpace: 'normal' }}>
                    <form onSubmit={(e) => void reset(e, u.email)} style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-end', gap: 12 }}>
                      <label className="field" style={{ flex: '1 1 260px' }}>Nouveau mot de passe provisoire pour {u.email}
                        <input type="text" value={resetPw} onChange={(e) => setResetPw(e.target.value)} autoComplete="off" spellCheck={false} minLength={MIN} autoFocus />
                      </label>
                      <button type="submit" className="btn btn-primary" disabled={busy || resetPw.length < MIN}>Valider</button>
                      <button type="button" className="btn" onClick={() => setResetFor(null)}>Annuler</button>
                    </form>
                  </td></tr>
                ),
              ]
            })}
          </tbody>
        </table>
      </div>
      <form onSubmit={add} style={{ padding: 20, borderTop: '1px solid var(--line-soft)', display: 'grid', gap: 12 }}>
        <h3 className="h3" style={{ margin: 0 }}>Ajouter un compte</h3>
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-end', gap: 12 }}>
          <label className="field" style={{ flex: '1 1 240px' }}>Adresse e-mail
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="off" spellCheck={false} autoCapitalize="none" maxLength={254} placeholder="prenom.nom@exemple.sn" />
          </label>
          <label className="field" style={{ flex: '1 1 200px' }}>Mot de passe provisoire
            <input type="text" value={temp} onChange={(e) => setTemp(e.target.value)} autoComplete="off" spellCheck={false} minLength={MIN} />
          </label>
          <label className="field" style={{ flex: '0 1 150px' }}>Droits
            <select className="select" style={{ height: 38 }} value={newRole} onChange={(e) => setNewRole(e.target.value as Role)}>
              {ROLES.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
            </select>
          </label>
          <button type="submit" className="btn btn-primary" disabled={busy || !email.trim() || temp.length < MIN}><IconPlus width={15} height={15} />Ajouter</button>
        </div>
        <p className="faint" style={{ margin: 0, fontSize: 12.5 }}>Au moins {MIN} caractères. La personne devra le remplacer par un mot de passe personnel à sa première connexion.</p>
        <p className="faint" style={{ margin: 0, fontSize: 12.5 }}>{ROLES.map((r) => `${r.label} : ${r.hint}`).join(' · ')}.</p>
      </form>
    </section>
  )
}

/** Libellés du journal : l'action brute reste lisible au survol (filtrage, export). */
const ACTION_LABEL: Record<string, string> = {
  login_ok: 'Connexion', login_failed: 'Échec de connexion', logout: 'Déconnexion',
  password_change: 'Mot de passe changé', password_change_failed: 'Changement de mot de passe refusé',
  user_add: 'Compte créé', user_remove: 'Compte retiré', user_role: 'Droits modifiés', user_reset: 'Mot de passe réinitialisé', user_passwd: 'Mot de passe changé (serveur)',
  settings: 'Paramètres modifiés', sync: 'Synchronisation',
  printer_add: 'Imprimante ajoutée', printer_edit: 'Imprimante modifiée', printer_remove: 'Imprimante retirée', printer_restore: 'Imprimante remise en service',
  incident_add: 'Incident déclaré', incident_resolve: 'Incident clôturé',
  technician_add: 'Technicien ajouté', technician_edit: 'Technicien modifié', ticket_assign: 'Ticket confié',
}
const FILTERS: { id: string; label: string }[] = [
  { id: '', label: 'Toutes les actions' }, { id: 'login', label: 'Connexions' }, { id: 'user', label: 'Comptes' },
  { id: 'password', label: 'Mots de passe' }, { id: 'printer', label: 'Imprimantes' }, { id: 'incident', label: 'Incidents' },
  { id: 'ticket', label: 'Tickets' }, { id: 'technician', label: 'Techniciens' }, { id: 'settings', label: 'Paramètres' }, { id: 'sync', label: 'Synchronisations' },
]
const ALERT_ACTIONS = new Set(['login_failed', 'password_change_failed'])

/** Journal d'audit : qui a fait quoi, sur quoi, depuis quel poste. Jamais de mot de passe. */
function AuditLog() {
  const [filter, setFilter] = useState<AuditFilter>({})
  const [userText, setUserText] = useState('')
  const [rows, setRows] = useState<AuditEntry[] | null>(null)
  const [more, setMore] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback(async (f: AuditFilter, before?: number) => {
    try {
      const r = await journal.list(f, before)
      setRows((cur) => (before && cur ? [...cur, ...r.entries] : r.entries)); setMore(r.more); setErr(null)
    } catch (x) { setErr(sentence((x as Error).message)) }
  }, [])
  useEffect(() => { void load(filter) }, [filter, load])

  return (
    <section className="panel" aria-labelledby="audit-title" style={{ flex: '1 1 100%', minWidth: 0 }}>
      <div className="panel-head" style={{ borderBottom: '1px solid var(--line-soft)', flexWrap: 'wrap' }}>
        <div><h2 id="audit-title" className="h2">Journal d'audit</h2><p className="sub">Connexions, comptes, paramètres, imprimantes, incidents et tickets : qui, quoi, quand, depuis quel poste.</p></div>
        <form onSubmit={(e) => { e.preventDefault(); setFilter({ ...filter, user: userText }) }} style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
          <input className="input" style={{ height: 32, width: 220 }} value={userText} onChange={(e) => setUserText(e.target.value)} placeholder="Compte (adresse exacte)" aria-label="Filtrer par compte" />
          <select className="select" style={{ height: 32 }} value={filter.action ?? ''} aria-label="Filtrer par type d'action"
            onChange={(e) => setFilter({ ...filter, user: userText, action: e.target.value })}>
            {FILTERS.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
          </select>
          <button type="submit" className="btn" style={{ height: 32 }}>Filtrer</button>
          <a className="btn" style={{ height: 32 }} href={journal.csvUrl(filter)} download>Exporter (CSV)</a>
        </form>
      </div>
      {err && <div style={{ padding: '16px 20px 0' }}><Feedback msg={{ ok: false, text: err }} /></div>}
      <div className="tbl-wrap">
        <table className="tbl">
          <thead><tr><th>Date</th><th>Compte</th><th>Action</th><th>Détail</th><th>Poste</th></tr></thead>
          <tbody>
            {!rows && !err && <tr><td colSpan={5}><div className="skeleton" style={{ height: 120 }} /></td></tr>}
            {rows?.length === 0 && <tr style={{ cursor: 'default' }}><td colSpan={5} className="muted">Aucune entrée pour ces filtres.</td></tr>}
            {rows?.map((r) => (
              <tr key={r.id} style={{ cursor: 'default' }}>
                <td className="muted">{fmtDate(r.ts)}</td>
                <td style={{ fontWeight: 500 }}>{r.user}</td>
                <td title={r.action} style={ALERT_ACTIONS.has(r.action) ? { color: 'var(--crit-text)' } : undefined}>{ACTION_LABEL[r.action] ?? r.action}</td>
                <td style={{ whiteSpace: 'normal', minWidth: 260 }}>{r.target}</td>
                <td className="muted">{r.source}</td>
              </tr>))}
          </tbody>
        </table>
      </div>
      {more && rows && <div style={{ padding: 16, textAlign: 'center' }}>
        <button type="button" className="btn" onClick={() => void load(filter, rows[rows.length - 1].id)}>Voir plus</button></div>}
    </section>
  )
}

export default function Accounts({ me, onMe, isAdmin }: { me: Me; onMe: (m: Me) => void; isAdmin: boolean }) {
  return (
    <>
      <div>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600, letterSpacing: '-.01em' }}>Comptes</h1>
        <p className="muted" style={{ margin: '4px 0 0', fontSize: 13.5 }}>{isAdmin ? 'Accès au tableau de bord PrintWatch et droits de chaque compte' : 'Votre accès au tableau de bord PrintWatch'}</p>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 24, alignItems: 'flex-start' }}>
        <OwnPassword me={me} onMe={onMe} />
        {isAdmin && <AccountList me={me} />}
        {isAdmin && <AuditLog />}
      </div>
    </>
  )
}
