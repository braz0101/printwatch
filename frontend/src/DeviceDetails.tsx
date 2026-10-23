import type { DeviceInfo, Printer } from './api'
import { ago, fmtDate } from './ui'

/* Libellés des codes normalisés (RFC 3805 / Host Resources MIB) renvoyés par l'API. */
const DEVICE_STATUS: Record<string, string> = { running: 'En fonctionnement', warning: 'Avertissement', down: 'En panne', testing: 'En test', unknown: 'Inconnu' }
const PRINTER_STATUS: Record<string, string> = { idle: 'Prête', printing: 'Impression en cours', warmup: 'Préchauffage', other: 'Autre', unknown: 'Inconnu' }
export const ERROR_LABEL: Record<string, string> = {
  lowPaper: 'Papier bas', noPaper: 'Plus de papier', lowToner: 'Consommable bas', noToner: 'Consommable épuisé', doorOpen: 'Capot ouvert',
  jammed: 'Bourrage papier', offline: 'Hors ligne', serviceRequested: 'Intervention requise', inputTrayMissing: "Bac d'alimentation absent",
  outputTrayMissing: 'Bac de sortie absent', markerSupplyMissing: 'Consommable absent', outputNearFull: 'Bac de sortie presque plein',
  outputFull: 'Bac de sortie plein', inputTrayEmpty: 'Bac vide', overduePreventMaint: 'Maintenance préventive dépassée',
}
const ALERT_LABEL: Record<number, string> = {
  3: 'Capot ouvert', 5: 'Verrou ouvert', 8: 'Bourrage', 9: 'Élément absent', 10: "Fin de vie proche d'un élément", 11: "Fin de vie d'un élément",
  12: 'Élément presque vide', 13: 'Élément vide', 14: 'Élément presque plein', 15: 'Élément plein', 22: 'Élément hors ligne',
  29: 'Panne récupérable', 30: 'Panne irrécupérable', 33: 'Panne moteur', 501: 'Porte ouverte', 801: "Bac d'alimentation absent",
  807: 'Papier bas', 808: 'Plus de papier', 809: 'Changement de papier demandé', 810: 'Alimentation manuelle demandée',
  901: 'Bac de sortie absent', 902: 'Bac de sortie presque plein', 903: 'Bac de sortie plein',
  1101: 'Toner vide', 1102: 'Encre vide', 1104: 'Toner presque vide', 1105: 'Encre presque vide',
  1107: 'Récupérateur de toner presque plein', 1108: 'Boîtier de maintenance presque plein', 1109: 'Récupérateur de toner plein',
  1110: 'Boîtier de maintenance plein', 1111: 'Tambour en fin de vie', 1112: "Tambour hors d'usage", 1115: 'Cartouche de toner absente',
  1301: 'Bac absent (circuit papier)', 1304: 'Recto verso impossible pour ce papier',
}
const GROUP_LABEL: Record<number, string> = { 5: 'général', 6: 'capot', 8: 'alimentation papier', 9: 'sortie papier', 10: 'impression', 11: 'consommables', 13: 'circuit papier' }
const SEVERITY: Record<string, { label: string; cls: string }> = {
  critical: { label: 'Critique', cls: 'v-crit' }, warning: { label: 'Avertissement', cls: 'v-warn' }, event: { label: 'Événement', cls: 'muted' }, other: { label: 'Information', cls: 'muted' },
}

export const nf = new Intl.NumberFormat('fr-FR')
export const fmtUptime = (s: number) => { const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600); return d ? `${d} j ${h} h` : `${h} h ${Math.floor((s % 3600) / 60)} min` }
const trayText = (t: DeviceInfo['trays'][number]) =>
  t.level == null || t.level === -2 ? 'Niveau inconnu' : t.level === -3 ? 'Papier présent' : t.level === 0 ? 'Vide'
    : t.max && t.max > 0 ? `${nf.format(t.level)} / ${nf.format(t.max)} feuilles` : `${nf.format(t.level)} feuilles`

/** Problèmes matériels signalés par l'imprimante (hors niveaux d'encre, déjà suivis) et capots ouverts. */
export function hardwareIssues(info: DeviceInfo | null | undefined): string[] {
  if (!info) return []
  const out = info.errors.filter((e) => e !== 'lowToner' && e !== 'noToner').map((e) => ERROR_LABEL[e] ?? e)
  if (info.covers.some((c) => c.status === 'open') && !out.includes('Capot ouvert')) out.push('Capot ouvert')
  return out
}
export const deviceStatusText = (info: DeviceInfo) => [info.device_status && DEVICE_STATUS[info.device_status], info.printer_status && PRINTER_STATUS[info.printer_status]].filter(Boolean).join(' · ')

/** Onglet « Matériel » : identité, état signalé, alertes, bacs et capots. */
export default function DeviceDetails({ p, now }: { p: Printer; now: number }) {
  const info = p.info
  if (!info) return <p className="muted" style={{ margin: 0, fontSize: 13.5 }}>Aucune fiche matérielle relevée{p.down || p.error ? " : l'imprimante ne répond pas." : ' pour l’instant ; elle apparaîtra au prochain cycle de collecte.'}</p>
  const issues = info.errors.map((e) => ERROR_LABEL[e] ?? e)
  const openCovers = info.covers.filter((c) => c.status === 'open')
  const knownCovers = info.covers.filter((c) => c.status === 'open' || c.status === 'closed')
  const hwWarn = info.device_status === 'warning' || info.device_status === 'down'

  return (
    <>
      <section aria-labelledby="hw-id">
        <h3 id="hw-id" className="h3">Identité</h3>
        <dl className="kv">
          {info.model && <><dt>Modèle exact</dt><dd>{info.model}</dd></>}
          {info.serial && <><dt>N° de série</dt><dd className="mono">{info.serial}</dd></>}
          {info.mac && <><dt>Adresse MAC</dt><dd className="mono">{info.mac}</dd></>}
          {p.host && <><dt>Adresse IP</dt><dd className="mono">{p.host}</dd></>}
          {info.sys_name && <><dt>Nom réseau</dt><dd className="mono">{info.sys_name}</dd></>}
          {info.sys_location && <><dt>Emplacement déclaré</dt><dd>{info.sys_location}</dd></>}
          {info.sys_contact && <><dt>Contact déclaré</dt><dd>{info.sys_contact}</dd></>}
          {info.uptime_s != null && <><dt>Allumée depuis</dt><dd>{fmtUptime(info.uptime_s)}</dd></>}
          {info.page_count != null && <><dt>Compteur de pages</dt><dd className="mono">{info.page_count.toLocaleString('fr-FR')}</dd></>}
          {p.pages_30d != null && <><dt>Pages sur 30 jours</dt><dd className="mono">{p.pages_30d.toLocaleString('fr-FR')}</dd></>}
        </dl>
      </section>

      <section aria-labelledby="hw-st">
        <h3 id="hw-st" className="h3">État signalé par l'imprimante</h3>
        <dl className="kv">
          {info.device_status && <><dt>État matériel</dt><dd className={info.device_status === 'down' ? 'v-crit' : hwWarn ? 'v-warn' : ''}>{DEVICE_STATUS[info.device_status]}</dd></>}
          {info.printer_status && <><dt>Activité</dt><dd>{PRINTER_STATUS[info.printer_status]}</dd></>}
          {info.display.length > 0 && <><dt>Écran</dt><dd>« {info.display.join(' · ')} »</dd></>}
          <dt>Indicateurs</dt><dd className={issues.length ? 'v-warn' : 'muted'}>{issues.length ? issues.join(' · ') : 'Aucun'}</dd>
        </dl>
      </section>

      {info.alerts.length > 0 && (
        <section aria-labelledby="hw-al">
          <h3 id="hw-al" className="h3">Alertes de l'imprimante</h3>
          <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {info.alerts.map((a, i) => {
              const sev = SEVERITY[a.severity] ?? SEVERITY.other
              return (
                <li key={i} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, padding: '8px 0', borderBottom: '1px solid var(--line-soft)', fontSize: 13.5 }}
                  title={`Code RFC 3805 : ${a.code}${a.description ? ` · message constructeur : ${a.description}` : ''}`}>
                  <span><b className={sev.cls}>{sev.label}</b> · {ALERT_LABEL[a.code] ?? `Code ${a.code}`}{a.group != null && GROUP_LABEL[a.group] && <span className="faint"> ({GROUP_LABEL[a.group]})</span>}</span>
                  {a.description && <span className="faint mono">{a.description}</span>}
                </li>
              )
            })}
          </ul>
        </section>
      )}

      {info.trays.length > 0 && (
        <section aria-labelledby="hw-tr">
          <h3 id="hw-tr" className="h3">Bacs papier</h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) 110px 150px', gap: '10px 16px', alignItems: 'center', fontSize: 13.5 }}>
            {info.trays.map((t, i) => {
              const pct = t.level != null && t.level >= 0 && t.max && t.max > 0 ? Math.min(100, (t.level / t.max) * 100) : null
              return [
                <span key={`n${i}`} style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.name}{t.media && t.media !== 'Any' && <span className="faint"> · {t.media}</span>}</span>,
                <span key={`b${i}`} className="track">{pct != null && <i style={{ width: `${pct}%`, background: 'var(--ok)' }} />}</span>,
                <span key={`v${i}`} className={t.level === 0 ? 'faint' : ''} style={{ textAlign: 'right' }}>{trayText(t)}</span>,
              ]
            })}
          </div>
        </section>
      )}

      {knownCovers.length > 0 && (
        <section aria-labelledby="hw-cv">
          <h3 id="hw-cv" className="h3">Capots</h3>
          {openCovers.length === 0
            ? <p className="muted" style={{ margin: 0, fontSize: 13.5 }}>Tous fermés : {knownCovers.map((c) => c.name).join(', ')}.</p>
            : <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13.5 }}>{openCovers.map((c) => <li key={c.name} className="v-crit">{c.name} : ouvert</li>)}</ul>}
        </section>
      )}

      <p className="faint" style={{ margin: 0, fontSize: 12 }} title={fmtDate(info.updated_at)}>Fiche relevée {ago(info.updated_at, now)}.</p>
    </>
  )
}
