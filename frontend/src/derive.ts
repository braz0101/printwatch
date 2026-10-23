/* Lecture métier de /api/status : état de chaque imprimante, actions à mener, ruptures, indicateurs. Aucun rendu ici. */
import type { Printer, Status, Supply } from './api'
import { hardwareIssues } from './DeviceDetails'
import { DAY, INK_LABEL, isBox, pretty, shortModel } from './ui'

/** État affiché : CRIT/WARN viennent des consommables, INC des problèmes signalés par l'imprimante, DOWN de l'absence de réponse. */
export type Kind = 'CRIT' | 'INC' | 'WARN' | 'OK' | 'DOWN' | 'NODATA'
export const KIND_LABEL: Record<Kind, string> = { CRIT: 'Critique', INC: 'Incident', WARN: 'Attention', OK: 'OK', DOWN: 'Injoignable', NODATA: 'Sans relevé' }
export const KIND_PLURAL: Record<Kind, string> = { CRIT: 'Critiques', INC: 'Incidents', WARN: 'Attention', OK: 'OK', DOWN: 'Injoignables', NODATA: 'Sans relevé' }
export const KIND_RANK: Record<Kind, number> = { CRIT: 0, INC: 1, WARN: 2, OK: 3, NODATA: 4, DOWN: 5 }

/** Filtre du tableau du parc : un état, ou `ALERT` (critiques, incidents et attention : ce qui demande une action). Défaut : `ALERT`. */
export type Filter = Kind | 'ALERT'
export const DEFAULT_FILTER: Filter = 'ALERT'
export function inFilter(p: Printer, f: Filter | null): boolean {
  if (!f) return true
  const k = kindOf(p)
  if (f === 'ALERT') return k === 'CRIT' || k === 'INC' || k === 'WARN'
  return k === f || (f === 'DOWN' && k === 'NODATA')            // « sans relevé » rangé avec les injoignables
}

export const INK_LETTER: Record<string, string> = { black: 'N', cyan: 'C', magenta: 'M', yellow: 'J' }
export const INK_ORDER = ['black', 'cyan', 'magenta', 'yellow']

const live = (s: Supply) => !s.stale
/** Problèmes à traiter hors niveaux : incidents saisis encore ouverts, puis ceux que l'imprimante signale elle-même. */
export const issuesOf = (p: Printer): string[] => [...(p.incidents ?? []).map((i) => i.category), ...hardwareIssues(p.info)]
/** Consommable sous un seuil : encre basse, ou boîtier de maintenance presque plein (son % est la place restante). */
export const isLow = (s: Supply) => live(s) && s.pct != null && s.alert !== 'OK'

export function kindOf(p: Printer): Kind {
  if (p.down || (p.error && !p.supplies.some(live))) return 'DOWN'
  if (p.state === 'CRIT') return 'CRIT'
  if (issuesOf(p).length) return 'INC'
  if (p.state === 'WARN') return 'WARN'
  if (p.state === 'OK') return 'OK'
  return 'NODATA'
}

/** Libellé d'un consommable bas : « magenta à 3 % » ; boîtier : « boîtier de maintenance plein à 88 % ». */
export function lowText(s: Supply): string {
  if (isBox(s)) return `boîtier de maintenance plein à ${Math.round(100 - (s.pct ?? 0))} %`
  const name = s.color && INK_LABEL[s.color] ? INK_LABEL[s.color].toLowerCase() : s.description
  return `${name} à ${Math.round(s.pct ?? 0)} %`
}
const cap = (t: string) => t.charAt(0).toUpperCase() + t.slice(1)

/** Rupture estimée la plus proche, en jours, parmi les consommables encore relevés. */
export function etaOf(p: Printer): number | null {
  const v = p.supplies.filter((s) => live(s) && s.forecast && !isBox(s)).map((s) => s.forecast!.days_left)
  return v.length ? Math.min(...v) : null
}
export const fmtDays = (d: number) => (d < 1 ? '< 1 j' : d > 365 ? '> 1 an' : `≈ ${Math.round(d)} j`)

export const placeOf = (p: Printer) => [pretty(p.location), shortModel(p.model) || shortModel(p.info?.model)].filter(Boolean).join(' · ')

export interface Todo { key: string; tone: 'crit' | 'warn' | 'down'; icon: 'crit' | 'warn' | 'inc' | 'down'; title: string; sub: string; printer?: string; order: number }

/** File « À traiter » : consommables critiques (rupture la plus proche d'abord), incidents, seuils d'attention, puis l'injoignabilité groupée. */
export function todos(st: Status): Todo[] {
  const out: Todo[] = []
  const now = st.generated_at
  for (const p of st.printers) {
    const k = kindOf(p)
    if (k === 'DOWN' || k === 'NODATA') continue
    const sub = [pretty(p.name), placeOf(p)].filter(Boolean).join(' · ')
    const low = p.supplies.filter(isLow).sort((a, b) => (a.pct ?? 0) - (b.pct ?? 0))
    const crit = low.filter((s) => s.alert === 'CRIT'), warn = low.filter((s) => s.alert === 'WARN')
    const eta = etaOf(p)
    if (crit.length) {
      const list = [...crit, ...warn].slice(0, 3).map(lowText).join(', ')
      out.push({ key: `${p.id}-c`, tone: 'crit', icon: 'crit', printer: p.id, sub, order: eta ?? 999,
        title: cap(list) + (eta != null ? ` · rupture ${fmtDays(eta)}` : '') })
    }
    for (const issue of issuesOf(p)) {
      out.push({ key: `${p.id}-i-${issue}`, tone: 'crit', icon: 'inc', printer: p.id, sub, order: 1000, title: issue })
    }
    if (!crit.length && warn.length) {
      out.push({ key: `${p.id}-w`, tone: 'warn', icon: 'warn', printer: p.id, sub, order: 2000 + (eta ?? 999),
        title: cap(warn.slice(0, 3).map(lowText).join(', ')) + (eta != null && eta <= 30 ? ` · rupture ${fmtDays(eta)}` : '') })
    }
  }
  out.sort((a, b) => a.order - b.order)
  const down = st.printers.filter((p) => kindOf(p) === 'DOWN')
  if (down.length) {
    const never = down.filter((p) => !p.last_seen).length
    const long = down.filter((p) => p.last_seen && now - p.last_seen > DAY).length
    const parts = [never && `${never} jamais relevée${never > 1 ? 's' : ''}`, long && `${long} muette${long > 1 ? 's' : ''} depuis plus de 24 h`].filter(Boolean)
    out.push({ key: 'down', tone: 'down', icon: 'down', order: 9999,
      title: `${down.length} imprimante${down.length > 1 ? 's' : ''} sans réponse`,
      sub: [...parts, "vérifier l'alimentation et le câble réseau"].join(' · ') })
  }
  return out
}

export interface EtaRow { key: string; printer: string; label: string; color: string; days: number }
/** Ruptures prévues dans les `horizon` prochains jours, de la plus proche à la plus lointaine. */
export function ruptures(st: Status, horizon = 30, limit = 8): EtaRow[] {
  const rows: EtaRow[] = []
  for (const p of st.printers) {
    if (kindOf(p) === 'DOWN') continue
    for (const s of p.supplies) {
      if (!live(s) || isBox(s) || !s.forecast || s.forecast.days_left > horizon) continue
      const ink = s.color && INK_LABEL[s.color] ? INK_LABEL[s.color].toLowerCase() : s.description
      rows.push({ key: `${p.id}-${s.key}`, printer: p.id, label: `${pretty(p.name)} · ${ink}`, color: `var(--ink-${s.color && INK_LABEL[s.color] ? s.color : 'other'})`, days: s.forecast.days_left })
    }
  }
  return rows.sort((a, b) => a.days - b.days).slice(0, limit)
}

export interface Kpis {
  total: number; online: number; down: number
  toOrder: number; toOrderCrit: number
  soon: number; next: EtaRow | null
  incidents: number; incidentText: string
  counts: Record<Kind, number>
}
export function kpis(st: Status): Kpis {
  const counts: Record<Kind, number> = { CRIT: 0, INC: 0, WARN: 0, OK: 0, DOWN: 0, NODATA: 0 }
  let toOrder = 0, toOrderCrit = 0
  const issues: Record<string, number> = {}
  for (const p of st.printers) {
    const k = kindOf(p)
    counts[k]++
    if (k === 'DOWN') continue
    for (const s of p.supplies.filter(isLow)) { toOrder++; if (s.alert === 'CRIT') toOrderCrit++ }
    for (const i of issuesOf(p)) issues[i] = (issues[i] ?? 0) + 1
  }
  const soonRows = ruptures(st, 14, 999)
  const inc = Object.values(issues).reduce((a, b) => a + b, 0)
  return {
    total: st.printers.length, down: counts.DOWN, online: st.printers.length - counts.DOWN - counts.NODATA,
    toOrder, toOrderCrit, soon: soonRows.length, next: soonRows[0] ?? null,
    incidents: inc, incidentText: Object.entries(issues).map(([t, n]) => `${n} ${t.toLowerCase()}`).join(' · '),
    counts,
  }
}

export const sortByUrgency = (a: Printer, b: Printer) =>
  KIND_RANK[kindOf(a)] - KIND_RANK[kindOf(b)] || (etaOf(a) ?? 9e9) - (etaOf(b) ?? 9e9) || a.name.localeCompare(b.name, 'fr')

export type GroupBy = 'none' | 'location' | 'model' | 'subnet'
export function groupKey(p: Printer, by: GroupBy): string {
  if (by === 'location') return pretty(p.location) || 'Emplacement non renseigné'
  if (by === 'model') return [p.brand, p.model].filter(Boolean).join(' ') || 'Modèle non renseigné'
  if (by === 'subnet') return p.host?.match(/^(\d+\.\d+\.\d+)\./)?.[1].concat('.0/24') ?? 'Autre'
  return ''
}

const pctOf = (p: Printer, color: string) => p.supplies.find((s) => s.color === color && live(s))?.pct
/** Export CSV (séparateur « ; », BOM UTF-8 : ouvre proprement dans Excel en français). */
export function toCsv(printers: Printer[]): string {
  const head = ['Imprimante', 'État', 'Emplacement', 'Utilisateur', 'Marque', 'Modèle', 'Adresse IP', 'N° de série', 'Noir %', 'Cyan %', 'Magenta %', 'Jaune %', 'Boîtier maintenance % plein', 'Rupture estimée (j)', 'Compteur pages', 'Pages 30 j', 'Dernier relevé']
  const cell = (v: unknown) => { const t = v == null ? '' : String(v); return /[;"\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t }
  const rows = printers.map((p) => {
    const box = p.supplies.find((s) => isBox(s) && live(s))
    const eta = etaOf(p)
    return [p.name, KIND_LABEL[kindOf(p)], p.location, p.user, p.brand, p.model, p.host ?? '', p.info?.serial ?? '',
      pctOf(p, 'black'), pctOf(p, 'cyan'), pctOf(p, 'magenta'), pctOf(p, 'yellow'),
      box?.pct != null ? Math.round(100 - box.pct) : '', eta != null ? Math.round(eta) : '',
      p.info?.page_count ?? '', p.pages_30d ?? '', p.last_seen ? new Date(p.last_seen * 1000).toLocaleString('fr-FR') : 'jamais']
  })
  return '﻿' + [head, ...rows].map((r) => r.map(cell).join(';')).join('\r\n')
}
export function download(name: string, text: string, type = 'text/csv;charset=utf-8') {
  const url = URL.createObjectURL(new Blob([text], { type }))
  const a = document.createElement('a')
  a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
