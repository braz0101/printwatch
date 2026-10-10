import { useMemo, useRef, useState } from 'react'
import { write, withAdmin, type Capabilities, type Ev, type Printer, type PrinterFields, type Status } from './api'
import { EventItem } from './EventLog'
import { IconClock, IconClose, IconPencil, IconTrash, IconUser, IconWarn, STATE_ICON } from './Icons'
import PrinterForm from './PrinterForm'
import { SupplyGauges } from './Tubes'
import { STATE_META, ago, fmtDate, pretty, supplyName } from './ui'
import { useDrawerFocus } from './useDrawerFocus'

interface Props {
  printer: Printer; status: Status; events: Ev[]; onClose: () => void
  /** Appelé après une modification (`removed` : l'imprimante a été retirée de la supervision). */
  onChanged: (what: 'edited' | 'removed') => void
}

/** Tiroir de détail d'une imprimante : éprouvettes, derniers événements ; édition et retrait si la gestion est activée. */
export default function Drawer({ printer: p, status, events, onClose, onChanged }: Props) {
  const { warn, crit } = status.thresholds
  const manage: Capabilities['manage'] = status.capabilities?.manage ?? 'off'
  const canEdit = manage !== 'off' && !!p.host
  const [editing, setEditing] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [delErr, setDelErr] = useState<string | null>(null)
  const panel = useRef<HTMLElement>(null)
  useDrawerFocus(panel, onClose)

  const meta = STATE_META[p.state], Icon = STATE_ICON[p.state]
  const mine = useMemo(() => events.filter((e) => e.printer === p.name).slice(0, 8), [events, p.name])
  const unknown = p.supplies.filter((s) => s.kind === 'consumable' && s.pct == null && s.status !== 'some_remaining')

  async function save(v: PrinterFields) {
    const patch: Partial<PrinterFields> = {}
    for (const k of Object.keys(v) as (keyof PrinterFields)[]) if (v[k] !== (p[k as keyof Printer] ?? '')) patch[k] = v[k]
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
        <header className="drawer-head">
          <div className="min-w-0">
            <h2 id="drawer-title" className="truncate text-[21px] font-semibold leading-tight tracking-tight">{editing ? "Modifier l'imprimante" : pretty(p.name)}</h2>
            {!editing && <>
              <p className="muted mt-0.5 truncate text-[13px]">{[pretty(p.location), p.model].filter(Boolean).join(' · ') || p.brand || '—'}</p>
              {p.user && <p className="muted mt-1 flex items-center gap-1.5 truncate text-[13px]"><IconUser width={13} height={13} className="shrink-0" />Utilisateur : <b className="font-semibold" style={{ color: 'var(--text)' }}>{p.user}</b></p>}
            </>}
          </div>
          {!editing && <span className="state-chip" style={{ color: meta.text, background: `color-mix(in srgb, ${meta.color} 14%, transparent)` }}><Icon width={14} height={14} />{meta.label}</span>}
          {canEdit && !editing && <button type="button" className="icon-btn" onClick={() => setEditing(true)} aria-label="Modifier l'imprimante" title="Modifier : utilisateur, emplacement, nom, adresse…"><IconPencil width={17} height={17} /></button>}
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Fermer le détail"><IconClose width={18} height={18} /></button>
        </header>

        <div className="drawer-body">
          {editing ? (
            <PrinterForm initial={{ name: p.name, host: p.host ?? '', brand: p.brand ?? '', model: p.model ?? '', location: p.location ?? '', user: p.user ?? '' }}
              submitLabel="Enregistrer" onSubmit={save} onCancel={() => { setEditing(false); setConfirm(false) }}>
              <div className="mt-2 border-t pt-4" style={{ borderColor: 'var(--line)' }}>
                {!confirm ? (
                  <button type="button" className="btn btn-danger" onClick={() => setConfirm(true)}><IconTrash width={15} height={15} />Retirer de la supervision…</button>
                ) : (
                  <div className="note flex-col !gap-2" role="alertdialog" aria-label="Confirmer le retrait">
                    <span><b>Retirer « {pretty(p.name)} » de la supervision ?</b> Elle disparaît du tableau de bord ; son historique est conservé et elle revient si vous ajoutez de nouveau la même adresse.</span>
                    {delErr && <span role="alert" style={{ color: 'var(--crit)' }}>{delErr}</span>}
                    <span className="flex gap-2"><button type="button" className="btn btn-danger" onClick={remove}>Oui, retirer</button><button type="button" className="btn" onClick={() => setConfirm(false)}>Garder</button></span>
                  </div>
                )}
              </div>
            </PrinterForm>
          ) : <>
            {p.down ? (
              <p className="note" title={p.error ?? undefined}>
                <IconClock width={15} height={15} className="shrink-0" />
                <span>Aucune réponse de l'imprimante{p.supplies.length > 0 ? ' : les niveaux ci-dessous sont périmés' : ''}{p.last_seen ? ` (dernier relevé ${ago(p.last_seen, status.generated_at)}, le ${fmtDate(p.last_seen)})` : ' (aucun relevé réussi depuis cette machine)'}. Contrôlez le câble réseau et l'alimentation.</span>
              </p>
            ) : p.last_seen != null && <p className="muted text-[12.5px]" title={fmtDate(p.last_seen)}>Dernier relevé {ago(p.last_seen, status.generated_at)}</p>}

            {!p.supplies.length ? <p className="muted py-6 text-center text-sm">Aucun relevé pour cette imprimante.</p> : (
              <section aria-label="Niveaux"><SupplyGauges p={p} warn={warn} crit={crit} /></section>
            )}

            {unknown.length > 0 && (
              <p className="flex items-start gap-1.5 text-[12.5px]" style={{ color: 'var(--warn)' }}>
                <IconWarn width={14} height={14} className="mt-0.5 shrink-0" />
                <span>Niveaux non remontés par l'imprimante : {unknown.map(supplyName).join(', ')}. Aucune alerte n'est possible sur ces consommables.</span>
              </p>
            )}

            {mine.length > 0 && (
              <section aria-label="Derniers événements">
                <h3 className="mb-1 text-[14.5px] font-semibold">Derniers événements</h3>
                <ul className="divide-y" style={{ borderColor: 'var(--line)' }}>{mine.map((e, i) => <EventItem key={`${e.ts}-${i}`} e={e} now={status.generated_at} showPrinter={false} />)}</ul>
              </section>
            )}
          </>}
        </div>
      </aside>
    </>
  )
}
