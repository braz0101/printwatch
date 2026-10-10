import { useRef } from 'react'
import { write, withAdmin, type Capabilities } from './api'
import { IconClose } from './Icons'
import PrinterForm from './PrinterForm'
import { useDrawerFocus } from './useDrawerFocus'

interface Props { manage: Capabilities['manage']; canSync: boolean; onClose: () => void; onAdded: (restored: boolean) => void }

/** Panneau d'ajout d'une imprimante. Si la synchronisation est disponible, un premier relevé est lancé aussitôt. */
export default function AddPanel({ manage, canSync, onClose, onAdded }: Props) {
  const panel = useRef<HTMLElement>(null)
  useDrawerFocus(panel, onClose)
  return (
    <>
      <div className="scrim" onClick={onClose} aria-hidden="true" />
      <aside ref={panel} tabIndex={-1} className="drawer" role="dialog" aria-modal="true" aria-labelledby="add-title">
        <header className="drawer-head">
          <div className="min-w-0"><h2 id="add-title" className="text-[21px] font-semibold leading-tight tracking-tight">Ajouter une imprimante</h2>
            <p className="muted mt-0.5 text-[13px]">Seuls le nom et l'adresse sont obligatoires.</p></div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Fermer"><IconClose width={18} height={18} /></button>
        </header>
        <div className="drawer-body">
          <PrinterForm submitLabel="Ajouter" onCancel={onClose}
            onSubmit={async (v) => { const r = await withAdmin(manage, () => write.create(v)); onAdded(r.restored) }} />
          <p className="muted text-[12.5px]">{canSync ? "Un premier relevé est lancé dès l'ajout." : "Le premier relevé aura lieu au prochain cycle de collecte."} L'imprimante doit répondre en SNMP (communauté configurée sur le serveur) ; sinon elle apparaîtra dans « sans données ».</p>
        </div>
      </aside>
    </>
  )
}
