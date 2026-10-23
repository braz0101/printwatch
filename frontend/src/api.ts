export type Alert = 'OK' | 'WARN' | 'CRIT'
export type State = 'down' | 'CRIT' | 'WARN' | 'OK' | 'NODATA'

export interface Supply {
  key: string; description: string; kind: string; color: string | null; status: string
  pct: number | null; alert: Alert; stale: boolean
  /** false : consommable non suivi (réglages) — affiché, mais jamais en alerte. */
  tracked?: boolean
  forecast: { days_left: number; confidence: string } | null
}
/** Fiche matérielle lue en SNMP au dernier relevé réussi (codes de la RFC 3805 ; libellés dans DeviceDetails). */
export interface DeviceInfo {
  updated_at: number
  sys_name?: string; sys_location?: string; sys_contact?: string; serial?: string; model?: string; mac?: string
  uptime_s?: number; page_count?: number
  device_status?: 'running' | 'warning' | 'down' | 'testing' | 'unknown'
  printer_status?: 'idle' | 'printing' | 'warmup' | 'other' | 'unknown'
  errors: string[]; display: string[]
  /** level : nombre de feuilles ; -3 = papier présent (quantité non précisée), -2 = inconnu. */
  trays: { name: string; media: string | null; level: number | null; max: number | null }[]
  covers: { name: string; status: 'open' | 'closed' | 'other' | 'unknown' }[]
  alerts: { severity: 'critical' | 'warning' | 'event' | 'other'; group: number | null; code: number; description: string | null }[]
}
export interface Printer {
  id: string; name: string; host?: string | null; brand: string | null; model: string | null; location: string | null; user: string | null
  down: boolean; last_seen: number | null; error: string | null; state: State; supplies: Supply[]
  info?: DeviceInfo | null; pages_30d?: number | null
  /** Incidents saisis encore ouverts (l'historique complet : incidents.list). */
  incidents?: OpenIncident[]
}
export interface Summary { printers: number; down: number; crit: number; warn: number; ok: number; nodata: number }
export type InkGroup = 'black' | 'cyan' | 'magenta' | 'yellow' | 'box' | 'other'
export interface Thresholds { warn: number; crit: number; inks?: InkGroup[] }
export interface Settings { warn: number; crit: number; inks: InkGroup[]; choices: InkGroup[] }
export type CollectorState = 'ok' | 'stale' | 'never' | 'all_failed'
/** Santé de la collecte SNMP : dernier cycle enregistré par le collecteur (et non l'heure de la réponse de l'API). */
export interface Collector { last_cycle: number | null; polled: number | null; failed: number | null; interval_s: number; state: CollectorState; running?: boolean }
/** sync : bouton de synchronisation disponible ; manage : gestion des imprimantes (off = lecture seule, open = libre, token = code administrateur). */
export type Role = 'lecture' | 'ecriture' | 'admin'
/** Ce que le compte connecté peut faire (calculé par le serveur selon son rôle ; le serveur revérifie chaque écriture).
 *  sync : synchroniser ; manage : gestion des imprimantes (off / open / token) ; incidents : déclarer et clôturer ; admin : comptes et paramètres. */
export interface Capabilities { sync: boolean; manage: 'off' | 'open' | 'token'; incidents?: boolean; admin?: boolean; role?: Role | null }
export interface OpenIncident { id: number; category: string; description: string; opened_at: number; opened_by: string }
export interface Incident extends OpenIncident { printer_id: string; resolved_at: number | null; resolved_by: string | null; resolution: string | null }
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

/* ---------------------------------- session ---------------------------------- */
/** Réponse du serveur quand la session manque ou a expiré : l'application revient alors à la page de connexion. */
const NO_SESSION = 'connexion requise'
export const SESSION_LOST = 'pw:session-lost'
const lost = (status: number, detail: unknown) => {
  if (status === 401 && detail === NO_SESSION) window.dispatchEvent(new Event(SESSION_LOST))
}

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url, { cache: 'no-store' })
  if (!r.ok) {
    const d = ((await r.json().catch(() => ({}))) as { detail?: string }).detail
    lost(r.status, d)
    throw new Error(d || String(r.status))
  }
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
    lost(r.status, d)
    if (r.status === 401 && d === NO_SESSION) throw new ApiError(0, 'Session expirée : reconnectez-vous.')    // pas le code administrateur
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

/* ---------------------------------- comptes ---------------------------------- */
/** auth = false : serveur sans authentification (démo locale). must_change : mot de passe provisoire à remplacer. */
export interface Me { auth: boolean; email: string | null; role: Role | null; must_change: boolean }
export interface Account { email: string; role: Role; must_change: boolean; created_at: number | null; updated_at: number | null }

/** Session absente → null (et non une erreur) : c'est l'état normal avant connexion. */
async function me(): Promise<Me | null> {
  const r = await fetch('/api/auth/me', { cache: 'no-store' })
  if (r.status === 401) return null
  if (!r.ok) throw new Error(String(r.status))
  return r.json() as Promise<Me>
}

export const auth = {
  me,
  login: (email: string, password: string, remember: boolean) => send<Me>('POST', '/api/auth/login', { email, password, remember }),
  logout: () => send<{ ok: boolean }>('POST', '/api/auth/logout'),
  changePassword: (current: string, next: string) => send<Me>('POST', '/api/auth/password', { current, new: next }),
  users: () => get<Account[]>('/api/users'),
  addUser: (email: string, password: string, role: Role) => send<{ email: string }>('POST', '/api/users', { email, password, role }),
  setRole: (email: string, role: Role) => send<{ email: string; role: Role }>('PATCH', `/api/users/${encodeURIComponent(email)}/role`, { role }),
  removeUser: (email: string) => send<{ removed: string }>('DELETE', `/api/users/${encodeURIComponent(email)}`),
  resetPassword: (email: string, password: string) => send<{ email: string }>('POST', `/api/users/${encodeURIComponent(email)}/password`, { password }),
}

/* ---------------------------------- journal d'audit (administrateurs) ---------------------------------- */
export interface AuditEntry { id: number; ts: number; user: string; action: string; target: string; source: string }
export interface AuditFilter { user?: string; action?: string }
const auditQuery = (f: AuditFilter, extra: Record<string, string> = {}) =>
  new URLSearchParams(Object.entries({ ...extra, user: f.user?.trim() ?? '', action: f.action ?? '' }).filter(([, v]) => v)).toString()
export const journal = {
  list: (f: AuditFilter, before?: number) =>
    get<{ entries: AuditEntry[]; more: boolean }>(`/api/audit?${auditQuery(f, { limit: '100', ...(before ? { before: String(before) } : {}) })}`),
  csvUrl: (f: AuditFilter) => `/api/audit.csv?${auditQuery(f)}`,
}

/* ---------------------------------- incidents saisis ---------------------------------- */
export const incidents = {
  categories: () => get<string[]>('/api/incidents/categories'),
  list: (printer: string) => get<Incident[]>(`/api/printers/${encodeURIComponent(printer)}/incidents`),
  add: (printer: string, category: string, description: string) =>
    send<Incident>('POST', `/api/printers/${encodeURIComponent(printer)}/incidents`, { category, description }),
  resolve: (id: number, resolution: string) => send<Incident>('POST', `/api/incidents/${id}/resolve`, { resolution }),
}

/* ---------------------------------- réglages des alertes ---------------------------------- */
export const settings = {
  get: () => get<Settings>('/api/settings'),
  save: (warn: number, crit: number, inks: InkGroup[]) => send<Settings>('PUT', '/api/settings', { warn, crit, inks }),
}

/* ---------------------------------- répartition des tickets d'assistance ---------------------------------- */
/**
 * Les tickets se répartissaient depuis l'intranet, qui est l'outil de tout le personnel ;
 * ils se gèrent ici, où seules les personnes du service informatique ont un compte.
 * Déposer une demande reste sur l'intranet.
 */
export interface Ticket {
  id: number
  objet: string
  date: string | null
  statut: number | null
  statut_nom: string
  priorite: number | null
  categorie: string | null
  /** Né d'une demande déposée sur l'intranet, et non d'un courriel ou d'un appel. */
  depuis_intranet: boolean
  /** Le nom affiché, quelle qu'en soit la source : GLPI d'abord, la table locale sinon. */
  confie_a: string | null
  /**
   * L'attribution n'a PAS pu être écrite dans GLPI, faute de compte pour l'attributaire.
   * La trace est alors un suivi dans la chronologie, et cette ligne est la seule réponse
   * à « qui tient ce ticket ».
   */
  hors_glpi: boolean
}
export interface Technicien {
  id: number
  name: string
  /** Vide : pas de compte GLPI, l'attribution se fera « hors GLPI ». */
  glpi_email: string | null
  active: number
  created_at: number
  created_by: string
}

export const tickets = {
  list: (intranet: boolean) =>
    get<{ tickets: Ticket[]; total: number }>(`/api/tickets?intranet=${intranet ? 'true' : 'false'}`),
  technicians: () => get<Technicien[]>('/api/technicians'),
  confier: (numero: number, technicianId: number) =>
    send<{ ticket: number; confie_a: string; hors_glpi: boolean }>(
      'POST', `/api/tickets/${numero}/confier`, { technician_id: technicianId }),
  ajouter: (name: string, glpiEmail: string) =>
    send<Technicien>('POST', '/api/technicians', { name, glpi_email: glpiEmail }),
  modifier: (id: number, champs: { name?: string; glpi_email?: string; active?: boolean }) =>
    send<Technicien>('PATCH', `/api/technicians/${id}`, champs),
}
