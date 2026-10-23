import { useMemo, useRef, useState } from 'react'
import { write, withAdmin, type Capabilities, type Ev, type Printer, type PrinterFields, type Status, type Supply } from './api'
import { type Kind, etaOf, fmtDays, isLow, issuesOf, kindOf, lowText, placeOf } from './derive'
import DeviceDetails, { deviceStatusText } from './DeviceDetails'
import { KindChip } from './FleetTable'
import History from './History'
import Incidents from './Incidents'
import { IconClose, IconCrit, IconDown, IconPencil, IconTool, IconTrash, IconWarn } from './Icons'
import PrinterForm from './PrinterForm'
import { DAY, INK_LABEL, INK_ORDER, ago, fmtDate, isBox, pretty } from './ui'
import { useDrawerFocus } from './useDrawerFocus'

interface Props {
  printer: Printer; status: Status; events: Ev[]; onClose: () => void
  /** Appelé après une modification (`removed` : l'imprimante a été retirée de la supervision ; `incident` : incident déclaré ou clôturé). */
  onChanged: (what: 'edited' | 'removed' | 'incident') => void
}
type Tab = 'syn' | 'inc' | 'hist' | 'hw' | 'log'
const TABS: [Tab, string][] = [['syn', 'Synthèse'], ['inc', 'Incidents'], ['hist', 'Historique'], ['hw', 'Matériel'], ['log', 'Journal']]

const EVENT: Record<string, { label: string; color: string }> = {
  critical: { label: 'Niveau critique', color: 'var(--crit)' }, warn: { label: 'Niveau bas', color: 'var(--warn-bar)' },
  resolved: { label: 'Retour à la normale', color: 'var(--ok)' }, replaced: { label: 'Cartouche remplacée', color: 'var(--accent)' },
  down: { label: 'Injoignable', color: 'var(--down)' }, up: { label: 'De nouveau joignable', color: 'var(--ok)' },
}

const supplyName = (s: Supply) => (isBox(s) ? 'Boîtier de maintenance' : (s.color && INK_LABEL[s.color]) || s.description)
const rank = (s: Supply) => (isBox(s) ? 90 : (() => { const i = INK_ORDER.indexOf(s.color ?? ''); return i < 0 ? 50 : i })())

/** Bandeau de tête : la chose la plus urgente à savoir sur cette imprimante, en une phrase. */
function Headline({ p, k, now }: { p: Printer; k: Kind; now: number }) {
  if (k === 'DOWN') {
    return <div className="note"><IconDown width={17} height={17} style={{ flex: 'none', marginTop: 1, color: 'var(--down)' }} />
      <span><b>Aucune réponse de l'imprimante</b>{p.last_seen ? ` depuis ${ago(p.last_seen, now).replace('il y a ', '')} (dernier relevé le ${fmtDate(p.last_seen)}). Les valeurs affichées datent de ce relevé.` : " : elle n'a encore jamais répondu."} Vérifier l'alimentation et le câble réseau{p.error ? ` · ${p.error}` : ''}.</span></div>
  }
  const low = p.supplies.filter(isLow).sort((a, b) => (a.pct ?? 0) - (b.pct ?? 0))
  const crit = low.filter((s) => s.alert === 'CRIT'), issues = issuesOf(p), eta = etaOf(p)
  if (crit.length || issues.length) {
    return <div className="banner" data-k="crit" role="alert">
      {crit.length ? <IconCrit width={18} height={18} style={{ flex: 'none', marginTop: 1 }} /> : <IconTool width={18} height={18} style={{ flex: 'none', marginTop: 1 }} />}
      <span>{crit.length > 0 && <><b>{crit.map(lowText).join(', ').replace(/^./, (c) => c.toUpperCase())}</b>{eta != null ? `, rupture estimée ${fmtDays(eta)} au rythme actuel` : ''}. À commander.</>}
        {issues.length > 0 && <> {crit.length ? 'Incident en cours : ' : <b>Incident en cours : </b>}{issues.join(', ').toLowerCase()}.</>}</span>
    </div>
  }
  if (low.length) return <div className="banner" data-k="warn"><IconWarn width={18} height={18} style={{ flex: 'none', marginTop: 1 }} /><span><b>{low.map(lowText).join(', ').replace(/^./, (c) => c.toUpperCase())}</b> : sous le seuil d'attention{eta != null && eta <= 30 ? `, rupture estimée ${fmtDays(eta)}` : ''}.</span></div>
  return null
}

function Synthese({ p, status, k }: { p: Printer; status: Status; k: Kind }) {
  const { warn, crit } = status.thresholds
  const list = [...p.supplies].sort((a, b) => rank(a) - rank(b))
  const unknown = list.filter((s) => !isBox(s) && s.pct == null && s.status !== 'some_remaining')
  const info = p.info
  return <>
    <Headline p={p} k={k} now={status.generated_at} />
    <section aria-labelledby="s-cons">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12 }}><h3 id="s-cons" className="h3">Consommables</h3><span className="faint" style={{ fontSize: 12 }}>Repères : {warn} % attention · {crit} % critique</span></div>
      {!list.length ? <p className="muted" style={{ margin: 0, fontSize: 13.5 }}>Aucun niveau relevé pour cette imprimante.</p> : (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(80px, 170px) minmax(56px, 1fr) auto auto', gap: '14px 14px', alignItems: 'center', fontSize: 13.5 }}>
          <span className="faint" style={{ fontSize: 12 }}>Consommable</span><span className="faint" style={{ fontSize: 12 }}>Niveau</span>
          <span className="faint" style={{ fontSize: 12, textAlign: 'right' }}>Reste</span><span className="faint" style={{ fontSize: 12, textAlign: 'right' }}>Rupture</span>
          {list.map((s) => {
            const box = isBox(s), stale = s.stale || k === 'DOWN'
            const shown = s.pct == null ? null : box ? 100 - s.pct : s.pct
            const cls = stale ? 'faint' : s.alert === 'CRIT' ? 'v-crit' : s.alert === 'WARN' ? 'v-warn' : ''
            const color = box ? 'var(--down)' : `var(--ink-${s.color && INK_LABEL[s.color] ? s.color : 'other'})`
            const val = s.pct == null ? (s.status === 'some_remaining' ? 'Présente' : 'Inconnu') : box ? `${Math.round(shown!)} % plein` : `${Math.round(s.pct)} %`
            const ticks = box ? [100 - warn, 100 - crit] : [warn, crit]
            return [
              <span key={`${s.key}n`} style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }} title={s.description}><span className="swatch" style={{ background: color }} /><span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{supplyName(s)}{s.tracked === false && <span className="faint" title="Non suivi dans les paramètres : aucune alerte"> · non suivi</span>}</span></span>,
              <span key={`${s.key}b`} style={{ position: 'relative', display: 'block' }}>
                <span className="track" style={{ height: 10, borderRadius: 5 }}>{shown != null && <i style={{ width: `${shown}%`, background: stale ? 'var(--ink-other)' : color, opacity: stale ? .5 : 1 }} />}</span>
                <span aria-hidden="true" style={{ position: 'absolute', left: `${ticks[0]}%`, top: -3, bottom: -3, width: 1, background: 'var(--warn)' }} />
                <span aria-hidden="true" style={{ position: 'absolute', left: `${ticks[1]}%`, top: -3, bottom: -3, width: 1, background: 'var(--crit)' }} />
              </span>,
              <span key={`${s.key}v`} className={cls} style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{val}</span>,
              <span key={`${s.key}e`} className={!stale && s.forecast && s.forecast.days_left <= 7 ? 'v-crit' : 'muted'} style={{ textAlign: 'right', whiteSpace: 'nowrap' }}
                title={s.forecast ? `Confiance de la prévision : ${s.forecast.confidence}` : undefined}>{!box && !stale && s.forecast ? fmtDays(s.forecast.days_left) : '—'}</span>,
            ]
          })}
        </div>
      )}
      {unknown.length > 0 && <p className="v-warn" style={{ margin: '12px 0 0', fontSize: 12.5, fontWeight: 400 }}>Niveaux non remontés par l'imprimante : {unknown.map(supplyName).join(', ')}. Aucune alerte n'est possible sur ces consommables.</p>}
    </section>

    <section aria-labelledby="s-id">
      <h3 id="s-id" className="h3">Fiche</h3>
      <dl className="kv">
        <dt>Emplacement</dt><dd>{pretty(p.location) || <span className="faint">Non renseigné</span>}</dd>
        <dt>Utilisateur</dt><dd>{p.user || <span className="faint">Non renseigné</span>}</dd>
        <dt>Modèle</dt><dd>{[p.brand, p.model].filter(Boolean).join(' ') || info?.model || '—'}</dd>
        {p.host && <><dt>Adresse IP</dt><dd className="mono">{p.host}</dd></>}
        {info?.serial && <><dt>N° de série</dt><dd className="mono">{info.serial}</dd></>}
        {info && deviceStatusText(info) && <><dt>État matériel</dt><dd>{deviceStatusText(info)}{info.display.length ? ` · écran : « ${info.display.join(' · ')} »` : ''}</dd></>}
      </dl>
    </section>
  </>
}

/** Volet de détail d'une imprimante : synthèse, historique, fiche matérielle et journal ; modification si la gestion est activée. */
export default function Drawer({ printer: p, status, events, onClose, onChanged }: Props) {
  const manage: Capabilities['manage'] = status.capabilities?.manage ?? 'off'
  const canEdit = manage !== 'off' && !!p.host
  const [tab, setTab] = useState<Tab>('syn')
  const [editing, setEditing] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [delErr, setDelErr] = useState<string | null>(null)
  const panel = useRef<HTMLElement>(null)
  useDrawerFocus(panel, onClose)
  const k = kindOf(p), now = status.generated_at
  const mine = useMemo(() => events.filter((e) => e.printer === p.name), [events, p.name])

  async function save(v: PrinterFields) {
    const patch: Partial<PrinterFields> = {}
    for (const f of Object.keys(v) as (keyof PrinterFields)[]) if (v[f] !== (p[f as keyof Printer] ?? '')) patch[f] = v[f]
    if (Object.keys(patch).length) await withAdmin(manage, () => write.update(p.id, patch))
    setEditing(false); onChanged('edited')
  }
  async function remove() {
    setDelErr(null)
    try { await withAdmin(manage, () => write.remove(p.id)); onChanged('removed') } catch (e) { setDelErr((e as Error).message) }
  }

  return (
    <>
      <div className="scrim" onClick={onClose} aria-hidden="true" />
      <aside ref={panel} tabIndex={-1} className="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">
        <header className="drawer-head" style={editing ? { paddingBottom: 18 } : undefined}>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              {!editing && <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}><KindChip k={k} />
                <span className="faint" style={{ fontSize: 12.5 }} title={p.last_seen ? fmtDate(p.last_seen) : undefined}>{p.last_seen ? `Relevé ${ago(p.last_seen, now)}` : 'Jamais relevée'}</span></div>}
              <h2 id="drawer-title" style={{ margin: editing ? 0 : '8px 0 2px', fontSize: 22, fontWeight: 600, letterSpacing: '-.01em' }}>{editing ? "Modifier l'imprimante" : pretty(p.name)}</h2>
              {!editing && <p className="muted" style={{ margin: 0, fontSize: 13.5 }}>{[placeOf(p), p.user].filter(Boolean).join(' · ') || '—'}{p.host && <> · <span className="mono">{p.host}</span></>}</p>}
            </div>
            {canEdit && !editing && <button type="button" className="btn" onClick={() => setEditing(true)}><IconPencil width={15} height={15} />Modifier</button>}
            <button type="button" className="btn btn-icon" onClick={onClose} aria-label="Fermer le détail"><IconClose width={16} height={16} /></button>
          </div>
          {!editing && (
            <div role="tablist" aria-label="Sections" className="tabs">
              {TABS.map(([id, label]) => <button key={id} type="button" role="tab" className="tab" aria-selected={tab === id} onClick={() => setTab(id)}>{label}{id === 'log' && mine.length > 0 && <span className="faint" style={{ marginLeft: 6, fontSize: 12 }}>{mine.length}</span>}{id === 'inc' && (p.incidents?.length ?? 0) > 0 && <span className="v-crit" style={{ marginLeft: 6, fontSize: 12 }}>{p.incidents!.length}</span>}</button>)}
            </div>
          )}
        </header>

        <div className="drawer-body" role={editing ? undefined : 'tabpanel'}>
          {editing ? (
            <PrinterForm initial={{ name: p.name, host: p.host ?? '', brand: p.brand ?? '', model: p.model ?? '', location: p.location ?? '', user: p.user ?? '' }}
              submitLabel="Enregistrer" onSubmit={save} onCancel={() => { setEditing(false); setConfirm(false) }}>
              <div style={{ marginTop: 8, paddingTop: 16, borderTop: '1px solid var(--line)' }}>
                {!confirm ? (
                  <button type="button" className="btn btn-danger" onClick={() => setConfirm(true)}><IconTrash width={15} height={15} />Retirer de la supervision…</button>
                ) : (
                  <div className="note" style={{ flexDirection: 'column', gap: 10 }} role="alertdialog" aria-label="Confirmer le retrait">
                    <span><b>Retirer « {pretty(p.name)} » de la supervision ?</b> Elle disparaît du tableau de bord ; son historique est conservé et elle revient si vous ajoutez de nouveau la même adresse.</span>
                    {delErr && <span role="alert" className="v-crit">{delErr}</span>}
                    <span style={{ display: 'flex', gap: 8 }}><button type="button" className="btn btn-danger" onClick={remove}>Oui, retirer</button><button type="button" className="btn" onClick={() => setConfirm(false)}>Garder</button></span>
                  </div>
                )}
              </div>
            </PrinterForm>
          ) : tab === 'syn' ? <Synthese p={p} status={status} k={k} />
            : tab === 'inc' ? <Incidents p={p} canWrite={!!status.capabilities?.incidents} onChanged={() => onChanged('incident')} />
            : tab === 'hist' ? <History p={p} warn={status.thresholds.warn} crit={status.thresholds.crit} />
            : tab === 'hw' ? <DeviceDetails p={p} now={now} />
            : (
              <section aria-label="Journal">
                {!mine.length ? <p className="muted" style={{ margin: 0, fontSize: 13.5 }}>Aucun événement récent pour cette imprimante.</p> : (
                  <ol style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                    {mine.map((e, i) => {
                      const m = EVENT[e.type] ?? { label: 'Événement', color: 'var(--text-3)' }
                      return (
                        <li key={`${e.ts}-${i}`} className="ev">
                          <span className="dot" style={{ background: m.color, width: 10, height: 10, marginTop: 4 }} />
                          <span><span style={{ display: 'block', fontSize: 13.5, fontWeight: 600 }}>{m.label}</span><span className="muted" style={{ display: 'block', fontSize: 12.5 }}>{e.message}</span></span>
                          <span className="faint" style={{ fontSize: 12.5, whiteSpace: 'nowrap' }} title={fmtDate(e.ts)}>{now - e.ts < DAY ? ago(e.ts, now) : fmtDate(e.ts)}</span>
                        </li>
                      )
                    })}
                  </ol>
                )}
              </section>
            )}
        </div>
      </aside>
    </>
  )
}
