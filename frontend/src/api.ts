export type Alert = 'OK' | 'WARN' | 'CRIT'
export type State = 'down' | 'CRIT' | 'WARN' | 'OK' | 'NODATA'

export interface Supply {
  key: string; description: string; kind: string; color: string | null; status: string
  pct: number | null; alert: Alert; stale: boolean
  forecast: { days_left: number; confidence: string } | null
}
export interface Printer {
  id: string; name: string; host?: string | null; brand: string | null; model: string | null; location: string | null; user: string | null
  down: boolean; last_seen: number | null; error: string | null; state: State; supplies: Supply[]
}
export interface Summary { printers: number; down: number; crit: number; warn: number; ok: number; nodata: number }
export interface Thresholds { warn: number; crit: number }
export type CollectorState = 'ok' | 'stale' | 'never' | 'all_failed'
/** Santé de la collecte SNMP : dernier cycle enregistré par le collecteur (et non l'heure de la réponse de l'API). */
export interface Collector { last_cycle: number | null; polled: number | null; failed: number | null; interval_s: number; state: CollectorState; running?: boolean }
/** sync : bouton de synchronisation disponible ; manage : gestion des imprimantes (off = lecture seule, open = libre, token = code administrateur). */
export interface Capabilities { sync: boolean; manage: 'off' | 'open' | 'token' }
export interface Status { generated_at: number; collector: Collector; capabilities: Capabilities; printers: Printer[]; summary: Summary; thresholds: Thresholds }
export interface Point { ts: number; pct: number }
export interface Ev { ts: number; type: string; printer: string; message: string; notified: boolean }

/** Série rééchantillonnée : pts = [fin de seau (epoch s), % restant]. Toutes les séries partagent la même grille. */
export interface Series { printer: string; key: string; pts: [number, number][]; replaced: number[] }
export interface SeriesResp { since: number; until: number; step: number; series: Series[] }
export interface Availability {
  since: number; until: number
  printers: { id: string; name: string; down_now: boolean; uptime_pct: number; down: [number, number][] }[]
}
export interface FleetDaily {
  days: number[]; thresholds: Thresholds
  printers: { id: string; name: string; states: State[]; min: (number | null)[]; down_hours: number[] }[]
  totals: { crit: number; warn: number; ok: number; down: number; nodata: number }[]
}

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url)
  if (!r.ok) throw new Error(((await r.json().catch(() => ({}))) as { detail?: string }).detail || String(r.status))
  return r.json() as Promise<T>
}

const enc = encodeURIComponent
export const api = {
  status: () => get<Status>('/api/status'),
  events: (limit = 15) => get<Ev[]>(`/api/events?limit=${limit}`),
  history: (printer: string, key: string, days: number) => get<Point[]>(`/api/history/${enc(printer)}/${enc(key)}?days=${days}`),
  series: (days: number, points: number, printer?: string) =>
    get<SeriesResp>(`/api/series?days=${days}&points=${points}${printer ? `&printer=${enc(printer)}` : ''}`),
  availability: (days: number) => get<Availability>(`/api/availability?days=${days}`),
  fleetDaily: (days: number) => get<FleetDaily>(`/api/fleet/daily?days=${days}`),
}

/* ---------------------------------- écriture : synchronisation et gestion des imprimantes ---------------------------------- */
export interface PrinterFields { name: string; host: string; brand: string; model: string; location: string; user: string }
export class ApiError extends Error { constructor(public status: number, message: string) { super(message) } }

const TOKEN_KEY = 'pw_admin_token'      // sessionStorage : oublié à la fermeture de l'onglet

async function send<T>(method: string, url: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = sessionStorage.getItem(TOKEN_KEY)
  if (token) headers['X-Admin-Token'] = token
  const r = await fetch(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
  if (!r.ok) {
    const d = ((await r.json().catch(() => ({}))) as { detail?: unknown }).detail
    const msg = typeof d === 'string' ? d : Array.isArray(d) ? `Champ invalide : ${String((d[0] as { loc?: unknown[] })?.loc?.slice(-1)[0] ?? '')}` : String(r.status)
    throw new ApiError(r.status, msg)
  }
  return r.json() as Promise<T>
}

/** Exécute une écriture ; en mode « token », demande le code administrateur (une fois par onglet) et le redemande s'il est refusé. */
export async function withAdmin<T>(manage: Capabilities['manage'], fn: () => Promise<T>): Promise<T> {
  if (manage === 'token' && !sessionStorage.getItem(TOKEN_KEY)) {
    const t = window.prompt('Code administrateur PrintWatch :')
    if (!t) throw new ApiError(0, 'Action annulée')
    sessionStorage.setItem(TOKEN_KEY, t)
  }
  try { return await fn() } catch (e) {
    if (!(e instanceof ApiError) || e.status !== 401) throw e
    sessionStorage.removeItem(TOKEN_KEY)
    const t = window.prompt('Code administrateur refusé. Réessayez :')
    if (!t) throw new ApiError(0, 'Action annulée')
    sessionStorage.setItem(TOKEN_KEY, t)
    return fn()
  }
}

export const write = {
  sync: () => send<{ state: 'started' | 'running' | 'too_soon' }>('POST', '/api/sync'),
  create: (b: PrinterFields) => send<{ id: string; restored: boolean }>('POST', '/api/printers', b),
  update: (id: string, b: Partial<PrinterFields>) => send<{ id: string }>('PATCH', `/api/printers/${encodeURIComponent(id)}`, b),
  remove: (id: string) => send<{ archived: boolean }>('DELETE', `/api/printers/${encodeURIComponent(id)}`),
}
