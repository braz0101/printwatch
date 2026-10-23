/**
 * Répartir les tickets d'assistance.
 *
 * POURQUOI CET ÉCRAN EST ICI. Les tickets se répartissaient depuis l'intranet, qui est
 * l'outil de tout le personnel ; ils se gèrent dans PrintWatch depuis le 21/10/2026, où
 * seules les personnes du service informatique ont un compte. Déposer une demande reste
 * sur l'intranet — c'est là que sont les agents.
 *
 * LA COLONNE « CONFIÉ À » A DEUX SOURCES, et c'est tout l'intérêt du tableau. GLPI fait
 * foi quand l'attributaire y a un compte ; sinon, seule la table de PrintWatch sait à qui
 * le ticket a été confié, et la pastille « hors GLPI » le dit. Un écran qui ne lirait que
 * GLPI afficherait « libre » sur un ticket pourtant attribué — et quelqu'un le reprendrait.
 *
 * LA LECTURE EST RÉSERVÉE AU RÔLE « écriture ». Ce n'est pas une sévérité gratuite : la
 * liste porte les intitulés des demandes de tout le personnel, parfois un nom ou un
 * message d'erreur. Le rôle « lecture » existe pour regarder des niveaux d'encre.
 */
import { useCallback, useEffect, useState } from 'react'
import { ApiError, tickets as apiTickets, type Technicien, type Ticket } from './api'
import { IconCrit, IconOk } from './Icons'
import { fmtDate } from './ui'

/** « 2026-10-20 11:26:59 » → « 20/10 ». La colonne est étroite, l'année se devine. */
function jourCourt(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso.replace(' ', 'T'))
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10)
  return `${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth() + 1).padStart(2, '0')}`
}

function Message({ msg }: { msg: { ok: boolean; text: string } | null }) {
  if (!msg) return null
  return msg.ok
    ? <p role="status" className="banner" data-k="ok" style={{ margin: 0 }}>
        <IconOk width={18} height={18} style={{ flex: 'none' }} />{msg.text}</p>
    : <p role="alert" className="banner" data-k="crit" style={{ margin: 0 }}>
        <IconCrit width={18} height={18} style={{ flex: 'none', marginTop: 1 }} />{msg.text}</p>
}

export default function Tickets({ isAdmin }: { isAdmin: boolean }) {
  const [liste, setListe] = useState<Ticket[] | null>(null)
  const [equipe, setEquipe] = useState<Technicien[]>([])
  const [intranetSeul, setIntranetSeul] = useState(false)
  const [chargement, setChargement] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  // Le ticket en cours d'attribution : il faut éteindre SON sélecteur, pas tous.
  const [encours, setEncours] = useState<number | null>(null)

  const charger = useCallback(async () => {
    setChargement(true)
    try {
      const [t, e] = await Promise.all([apiTickets.list(intranetSeul), apiTickets.technicians()])
      setListe(t.tickets)
      setEquipe(e)
      setMsg(null)
    } catch (e) {
      // ON NE MONTRE PAS UNE LISTE VIDE EN SILENCE : elle ferait croire qu'il n'y a rien
      // à faire. Le serveur refuse explicitement quand GLPI n'est pas configuré.
      setListe([])
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) })
    } finally {
      setChargement(false)
    }
  }, [intranetSeul])

  useEffect(() => { void charger() }, [charger])

  const confier = async (numero: number, technicianId: number) => {
    setEncours(numero)
    try {
      const r = await apiTickets.confier(numero, technicianId)
      setMsg({
        ok: true,
        text: r.hors_glpi
          // ON DIT CE QUI S'EST VRAIMENT PASSÉ. Sans cette phrase, on croirait que GLPI
          // porte l'attribution, et on s'étonnerait de ne pas l'y voir.
          ? `Ticket #${numero} confié à ${r.confie_a}. Il n'a pas de compte GLPI : la trace est un suivi dans le ticket.`
          : `Ticket #${numero} confié à ${r.confie_a} dans GLPI.`,
      })
      await charger()
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : String(e) })
    } finally {
      setEncours(null)
    }
  }

  const actifs = equipe.filter((t) => t.active)

  return (
    <section className="panel" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div className="panel-head" style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <div>
          <h2 className="h3" style={{ margin: 0 }}>Répartir les tickets</h2>
          <p className="sub" style={{ margin: 0 }}>
            Les demandes d'assistance ouvertes dans GLPI, et qui les tient.
          </p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <label className="faint" style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
            <input type="checkbox" checked={intranetSeul} onChange={(e) => setIntranetSeul(e.target.checked)} />
            Seulement les demandes déposées depuis l'intranet
          </label>
          <button type="button" className="btn" onClick={() => void charger()} disabled={chargement}>
            {chargement ? 'Chargement…' : 'Rafraîchir'}
          </button>
        </div>
      </div>

      <Message msg={msg} />

      {/* LE CARNET VIDE EST UN CAS À NOMMER, pas une colonne grisée : sans technicien,
          aucun sélecteur n'aboutit, et un écran muet laisserait chercher la panne. */}
      {liste !== null && actifs.length === 0 && (
        <p className="banner" data-k="warn" style={{ margin: 0 }}>
          Aucun technicien ne peut recevoir de ticket.{' '}
          {isAdmin ? 'Ajoutez-en un ci-dessous.' : 'Demandez à un administrateur d’en ajouter.'}
        </p>
      )}

      {liste === null ? (
        <div className="skeleton" style={{ height: 120 }} />
      ) : liste.length === 0 ? (
        <p className="muted" style={{ margin: 0 }}>
          {intranetSeul
            ? 'Aucune demande ouverte déposée depuis l’intranet.'
            : 'Aucun ticket ouvert.'}
        </p>
      ) : (
        <div className="tbl-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>Ticket</th><th>Objet</th><th>État</th><th>Confié à</th><th>Confier à</th>
              </tr>
            </thead>
            <tbody>
              {liste.map((t) => (
                <tr key={t.id}>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <b>#{t.id}</b>{' '}
                    <span className="faint">{jourCourt(t.date)}</span>{' '}
                    {t.depuis_intranet && <span className="chip" title="Déposée depuis l'intranet">intranet</span>}
                  </td>
                  <td>
                    {t.objet}
                    {t.categorie && <div className="faint">{t.categorie}</div>}
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>{t.statut_nom}</td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    {t.confie_a ? (
                      <>
                        {t.confie_a}{' '}
                        {t.hors_glpi && (
                          <span className="chip" title="Sans compte GLPI : la trace est un suivi dans le ticket">
                            hors GLPI
                          </span>
                        )}
                      </>
                    ) : (
                      <span className="muted">libre</span>
                    )}
                  </td>
                  <td>
                    <select
                      className="select"
                      value=""
                      disabled={encours === t.id || actifs.length === 0}
                      onChange={(e) => { if (e.target.value) void confier(t.id, Number(e.target.value)) }}
                      aria-label={`Confier le ticket ${t.id}`}
                    >
                      <option value="">{encours === t.id ? 'Envoi…' : 'Choisir…'}</option>
                      {actifs.map((x) => (
                        <option key={x.id} value={x.id}>
                          {x.name}{x.glpi_email ? '' : ' (hors GLPI)'}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {isAdmin && <Carnet equipe={equipe} onChange={charger} />}
    </section>
  )
}

/**
 * Qui peut recevoir un ticket.
 *
 * TENU À LA MAIN, ET NON DÉDUIT DE GLPI : il faut pouvoir y inscrire quelqu'un QUI N'A
 * PAS DE COMPTE GLPI — un stagiaire, un prestataire de passage. L'API de GLPI ne connaît
 * que ses propres comptes.
 *
 * PAS DE DATE DE FIN, un simple interrupteur. Un carnet qui se vide tout seul à une date
 * que personne ne relit se constate trop tard.
 */
function Carnet({ equipe, onChange }: { equipe: Technicien[]; onChange: () => Promise<void> }) {
  const [nom, setNom] = useState('')
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const ajouter = async () => {
    setBusy(true)
    try {
      await apiTickets.ajouter(nom.trim(), email.trim())
      setNom(''); setEmail('')
      setMsg({ ok: true, text: 'Technicien ajouté.' })
      await onChange()
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  const basculer = async (t: Technicien) => {
    try {
      await apiTickets.modifier(t.id, { active: !t.active })
      await onChange()
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : String(e) })
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, borderTop: '1px solid var(--line)', paddingTop: 16 }}>
      <div>
        <h3 className="h3" style={{ margin: 0 }}>Qui peut recevoir un ticket</h3>
        <p className="sub" style={{ margin: 0 }}>
          L'adresse GLPI est facultative. Sans elle, l'attribution ne peut pas être écrite
          dans GLPI : elle devient un suivi dans la chronologie du ticket.
        </p>
      </div>

      <Message msg={msg} />

      {equipe.length > 0 && (
        <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr><th>Nom</th><th>Adresse GLPI</th><th>Reçoit</th><th /></tr></thead>
            <tbody>
              {equipe.map((t) => (
                <tr key={t.id}>
                  <td>{t.name}</td>
                  <td>{t.glpi_email || <span className="chip">hors GLPI</span>}</td>
                  <td>{t.active ? 'oui' : <span className="muted">non</span>}</td>
                  <td style={{ textAlign: 'right' }}>
                    <button type="button" className="btn" onClick={() => void basculer(t)}>
                      {t.active ? 'Retirer' : 'Réactiver'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'flex-end' }}>
        <label className="field" style={{ flex: '1 1 180px' }}>
          <span>Nom</span>
          <input value={nom} onChange={(e) => setNom(e.target.value)} placeholder="Prénom Nom" />
        </label>
        <label className="field" style={{ flex: '1 1 220px' }}>
          <span>Adresse GLPI <span className="faint">(facultative)</span></span>
          <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="prenom.nom@exemple.sn" />
        </label>
        <button type="button" className="btn btn-primary" disabled={busy || nom.trim().length < 2}
                onClick={() => void ajouter()}>
          Ajouter
        </button>
      </div>
    </div>
  )
}
