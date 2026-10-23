import { useCallback, useEffect, useRef, useState } from 'react'
import type { Printer, State, Supply } from './api'

export const prefersReducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false

export const DAY = 86400
export const fmtDate = (ts: number) => new Date(ts * 1000).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' })
export const fmtDay = (ts: number) => new Date(ts * 1000).toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' })
/** Jour calendaire UTC : les agrégats quotidiens du serveur sont découpés en jours UTC. */
export const fmtDayUTC = (ts: number) => new Date(ts * 1000).toLocaleDateString('fr-FR', { day: 'numeric', month: 'short', timeZone: 'UTC' })
export const fmtTime = (ts: number) => new Date(ts * 1000).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })

/** « il y a 3 h » : l'horodatage exact reste disponible en infobulle. */
export function ago(ts: number, now = Date.now() / 1000): string {
  const d = Math.max(0, now - ts)
  if (d < 90) return "à l'instant"
  if (d < 3600) return `il y a ${Math.round(d / 60)} min`
  if (d < DAY) return `il y a ${Math.round(d / 3600)} h`
  return `il y a ${Math.round(d / DAY)} j`
}

export const STATE_META: Record<State, { label: string; plural: string; color: string; text: string }> = {
  CRIT: { label: 'Critique', plural: 'Critiques', color: 'var(--crit)', text: 'var(--crit)' },
  WARN: { label: 'Attention', plural: 'Attention', color: 'var(--warn-bar)', text: 'var(--warn)' },
  down: { label: 'Injoignable', plural: 'Injoignables', color: 'var(--down)', text: 'var(--muted)' },
  OK: { label: 'OK', plural: 'OK', color: 'var(--ok)', text: 'var(--ok)' },
  NODATA: { label: 'Sans relevé', plural: 'Sans relevé', color: 'var(--faint)', text: 'var(--muted)' },
}
export const STATE_RANK: Record<State, number> = { CRIT: 0, WARN: 1, down: 2, OK: 3, NODATA: 4 }
export const ALERT_COLOR = { CRIT: 'var(--crit)', WARN: 'var(--warn-bar)', OK: 'var(--ok)' } as const
export const alertText = (a: 'OK' | 'WARN' | 'CRIT') => (a === 'OK' ? 'var(--text)' : a === 'WARN' ? 'var(--warn)' : 'var(--crit)')

export const INK_ORDER = ['black', 'cyan', 'magenta', 'yellow']
export const INK_LABEL: Record<string, string> = { black: 'Noir', cyan: 'Cyan', magenta: 'Magenta', yellow: 'Jaune' }
export const inkColor = (c: string | null) => `var(--ink-${c && INK_LABEL[c] ? c : 'other'})`
export const CONFIDENCE: Record<string, string> = { high: 'élevée', medium: 'moyenne', low: 'faible' }

/** Boîtier de maintenance : il se REMPLIT. L'API renvoie la place restante (comme pour les encres) ; l'écran parle de remplissage. */
export const isBox = (s: Supply) => s.kind === 'receptacle'
export const supplyName = (s: Supply) => (isBox(s) ? 'Boîtier de maintenance' : (s.color && INK_LABEL[s.color]) || s.description)
/** Niveau tel qu'on le dessine : encre = quantité restante ; boîtier = remplissage. */
export const shownLevel = (s: Supply) => (s.pct == null ? null : isBox(s) ? 100 - s.pct : s.pct)
export const levelText = (s: Supply) =>
  s.pct == null ? (s.status === 'some_remaining' ? 'Présente' : '—') : isBox(s) ? `Plein à ${Math.round(100 - s.pct)} %` : `${Math.round(s.pct)} %`
export const isPresentOnly = (s: Supply) => s.pct == null && s.status === 'some_remaining'

export const inkRank = (s: Supply) => { const i = INK_ORDER.indexOf(s.color ?? ''); return i < 0 ? 99 : i }
/** Consommable le plus bas (en pourcentage restant), toutes catégories confondues : c'est lui qui déclenche l'alerte. */
export const lowestSupply = (p: Printer): Supply | null =>
  p.supplies.reduce<Supply | null>((m, s) => (s.pct != null && (!m || s.pct < m.pct!) ? s : m), null)
/** Rupture estimée la plus proche (jours) ; null si injoignable ou sans prévision. */
export const etaDays = (p: Printer): number | null => {
  const v = p.supplies.filter((s) => !s.stale && s.forecast).map((s) => s.forecast!.days_left)
  return v.length ? Math.min(...v) : null
}
export const fmtEta = (d: number) => (d < 1 ? '< 1 j' : d > 365 ? '> 1 an' : `≈ ${Math.round(d)} j`)
export const plural = (n: number, one: string, many = one + 's') => (n > 1 ? many : one)
export const shortName = (p: Printer) => p.name

/** Abréviations développées à l'affichage (noms et emplacements). À compléter ici, sans toucher aux données. */
const ABBR: Record<string, string> = { BAT: 'Bâtiment', ADM: 'Administration', INSP: 'Inspection', RDC: 'Rez-de-chaussée', APPRO: 'approvisionnement', '1IER': '1er' }
/** « BAT B RDC » → « Bâtiment B Rez-de-chaussée » ; les sigles de 3 lettres ou moins (ADV, RH, DSI) sont conservés. */
/** Mots de liaison saisis en majuscules (« CONTRÔLE DE GESTION ») : remis en minuscules, sauf en tête. */
const STOP: Record<string, string> = { DE: 'de', DU: 'du', DES: 'des', LA: 'la', LE: 'le', LES: 'les', ET: 'et', EN: 'en', AU: 'au', AUX: 'aux', SUR: 'sur', PAR: 'par' }
export const pretty = (s: string | null | undefined): string =>
  (s ?? '').replace(/[\p{L}\p{N}]+/gu, (w, offset: number) => {
    const up = w.toUpperCase()
    if (ABBR[up]) return ABBR[up]
    if (offset > 0 && w === up && STOP[up]) return STOP[up]
    return w.length > 3 && w === up ? w[0] + w.slice(1).toLowerCase() : w
  }).replace(/^\p{Ll}/u, (c) => c.toUpperCase())

/** Modèle lisible : « EPSON WorkForce Pro WF-C5890DWF 24ppm A4 Color MFP » → « Epson WF-C5890DWF » ; « Color LaserJet Pro MFP M479fdn » → « HP M479fdn ». */
export function shortModel(m: string | null | undefined): string {
  if (!m) return ''
  const wf = m.match(/\bWF-[A-Z0-9]+/i)
  if (wf) return `Epson ${wf[0].toUpperCase()}`
  const hp = m.match(/\b(M\d{3}[a-z]*)\b/i)
  if (hp && /laserjet|\bhp\b/i.test(m)) return `HP ${hp[1]}`
  return m.replace(/\s+\d+\s*ppm.*$/i, '').replace(/\.\s*must order.*$/i, '').trim()
}

/** Valeur qui monte progressivement vers sa cible (désactivé si l'utilisateur réduit les animations). */
export function useCountUp(target: number, ms = 800): number {
  const [v, setV] = useState(prefersReducedMotion() ? target : 0)
  const from = useRef(0)
  useEffect(() => {
    if (prefersReducedMotion()) { setV(target); return }
    const start = performance.now(), a = from.current
    let raf = 0
    const tick = (now: number) => {
      const p = Math.min(1, (now - start) / ms), e = 1 - Math.pow(1 - p, 3)
      const val = a + (target - a) * e
      setV(val); from.current = val
      if (p < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [target, ms])
  return Math.round(v)
}

/** Faux jusqu'au premier rendu peint : permet de lancer les transitions de remplissage au chargement. */
export function useMounted(): boolean {
  const [m, setM] = useState(false)
  useEffect(() => { const id = requestAnimationFrame(() => requestAnimationFrame(() => setM(true))); return () => cancelAnimationFrame(id) }, [])
  return m
}

/* ---------------------------------- navigation par fragment d'URL ----------------------------------
   Une seule page : `#/`. `#/?p=<id>&k=<consommable>` ouvre le tiroir d'une imprimante ; le bouton « retour »
   du navigateur le ferme. Les anciens liens (`#/parc?p=…`, `#/journal`) restent acceptés. */
const parseHash = (): { path: string; printer: string | null; key: string | null } => {
  const [path, qs = ''] = (window.location.hash.replace(/^#/, '') || '/').split('?')
  const q = new URLSearchParams(qs)
  return { path, printer: q.get('p'), key: q.get('k') }
}
export function useRoute() {
  const [r, setR] = useState(parseHash)
  useEffect(() => {
    const f = () => setR(parseHash())
    window.addEventListener('hashchange', f)
    return () => window.removeEventListener('hashchange', f)
  }, [])
  const go = useCallback((printer: string | null = null, key: string | null = null) => {
    window.location.hash = printer ? `#/?p=${encodeURIComponent(printer)}${key ? `&k=${encodeURIComponent(key)}` : ''}` : '#/'
  }, [])
  return { ...r, go }
}
