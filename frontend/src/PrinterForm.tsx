import { useState, type FormEvent, type ReactNode } from 'react'
import type { PrinterFields } from './api'

const EMPTY: PrinterFields = { name: '', host: '', brand: '', model: '', location: '', user: '' }

interface Props {
  initial?: Partial<PrinterFields>
  submitLabel: string
  onSubmit: (values: PrinterFields) => Promise<void>
  onCancel: () => void
  children?: ReactNode          // zone sous les boutons (ex. retrait)
}

/** Formulaire commun d'ajout et d'édition d'une imprimante. Aucune requête ici : `onSubmit` fait l'appel et lève une Error lisible. */
export default function PrinterForm({ initial, submitLabel, onSubmit, onCancel, children }: Props) {
  const [v, setV] = useState<PrinterFields>({ ...EMPTY, ...Object.fromEntries(Object.entries(initial ?? {}).map(([k, x]) => [k, x ?? ''])) })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const set = (k: keyof PrinterFields) => (e: React.ChangeEvent<HTMLInputElement>) => setV((o) => ({ ...o, [k]: e.target.value }))

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true); setErr(null)
    try { await onSubmit({ ...v, name: v.name.trim(), host: v.host.trim() }) } catch (x) { setErr((x as Error).message); setBusy(false) }
  }

  return (
    <form onSubmit={submit} className="grid gap-4" noValidate>
      <label className="field">Nom de l'imprimante *<input value={v.name} onChange={set('name')} maxLength={80} required autoFocus placeholder="Pool Comptabilité" /></label>
      <label className="field">Adresse IP ou nom d'hôte *<input value={v.host} onChange={set('host')} maxLength={253} required inputMode="decimal" placeholder="198.51.100.16" spellCheck={false} /></label>
      <label className="field">Emplacement<input value={v.location} onChange={set('location')} maxLength={120} placeholder="Bâtiment Administration" /></label>
      <label className="field">Utilisateur<input value={v.user} onChange={set('user')} maxLength={80} placeholder="NOM Prénom" /></label>
      <div className="grid grid-cols-2 gap-3">
        <label className="field">Marque<input value={v.brand} onChange={set('brand')} maxLength={40} placeholder="EPSON" /></label>
        <label className="field">Modèle<input value={v.model} onChange={set('model')} maxLength={60} placeholder="WF-C879RDWF" /></label>
      </div>
      {err && <p role="alert" className="note" style={{ color: 'var(--crit)' }}>{err}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <button type="submit" className="btn btn-primary" disabled={busy || !v.name.trim() || !v.host.trim()}>{busy ? 'Enregistrement…' : submitLabel}</button>
        <button type="button" className="btn" onClick={onCancel} disabled={busy}>Annuler</button>
      </div>
      {children}
    </form>
  )
}
