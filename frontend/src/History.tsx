import { useEffect, useMemo, useRef, useState } from 'react'
import { api, type Printer, type SeriesResp } from './api'
import { INK_LABEL, fmtDate, fmtDay } from './ui'

const W = 664, H = 260, L = 40, R = 64, T = 12, B = 28
const PERIODS = [[30, '30 j'], [90, '90 j'], [365, '1 an']] as const

/** Niveaux d'encre dans le temps : une courbe par encre, seuils en pointillés, remplacements marqués, survol au jour près. */
export default function History({ p, warn, crit }: { p: Printer; warn: number; crit: number }) {
  const [days, setDays] = useState<number>(30)
  const [data, setData] = useState<SeriesResp | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [hover, setHover] = useState<number | null>(null)
  const svg = useRef<SVGSVGElement>(null)

  useEffect(() => {
    let live = true
    setData(null); setErr(null)
    api.series(days, days <= 30 ? 120 : 180, p.id).then((d) => live && setData(d)).catch((e) => live && setErr((e as Error).message))
    return () => { live = false }
  }, [days, p.id])

  const lines = useMemo(() => {
    if (!data) return []
    const byKey = new Map(p.supplies.map((s) => [s.key, s]))
    return data.series.map((s) => ({ s, sup: byKey.get(s.key) })).filter((x) => x.sup && x.sup.kind === 'consumable' && x.s.pts.length)
      .map(({ s, sup }) => ({ key: s.key, label: sup!.color && INK_LABEL[sup!.color] ? INK_LABEL[sup!.color] : sup!.description,
        color: `var(--ink-${sup!.color && INK_LABEL[sup!.color] ? sup!.color : 'other'})`, pts: s.pts, replaced: s.replaced }))
  }, [data, p.supplies])

  if (err) return <p className="note">Historique indisponible : {err}</p>
  if (!data) return <div className="skeleton" style={{ height: H + 60 }} />

  const x = (ts: number) => L + ((ts - data.since) / (data.until - data.since)) * (W - L - R)
  const y = (v: number) => T + (1 - v / 100) * (H - T - B)
  const gap = data.step * 1.6
  const path = (pts: [number, number][]) => pts.map(([ts, v], i) => `${i && ts - pts[i - 1][0] <= gap ? 'L' : 'M'}${x(ts).toFixed(1)} ${y(v).toFixed(1)}`).join(' ')
  const grid = [...new Set(lines.flatMap((l) => l.pts.map((q) => q[0])))].sort((a, b) => a - b)
  const ticks = [0, 1 / 3, 2 / 3, 1].map((f) => data.since + f * (data.until - data.since))
  const reps = lines.flatMap((l) => l.replaced.map((ts) => ({ ts, label: l.label })))

  function move(e: React.PointerEvent<SVGSVGElement>) {
    const box = svg.current!.getBoundingClientRect()
    const px = ((e.clientX - box.left) / box.width) * W
    if (!grid.length) return
    const ts = data!.since + ((px - L) / (W - L - R)) * (data!.until - data!.since)
    setHover(grid.reduce((a, b) => (Math.abs(b - ts) < Math.abs(a - ts) ? b : a)))
  }
  const at = hover == null ? [] : lines.map((l) => ({ l, v: l.pts.find((q) => q[0] === hover)?.[1] })).filter((r) => r.v != null)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <h3 className="h3" style={{ margin: 0 }}>Niveaux d'encre</h3>
        <div role="group" aria-label="Période" style={{ display: 'inline-flex', gap: 2 }}>
          {PERIODS.map(([d, l]) => <button key={d} type="button" className="seg" aria-pressed={days === d} onClick={() => setDays(d)}>{l}</button>)}
        </div>
      </div>
      {!lines.length ? <p className="muted" style={{ margin: 0, fontSize: 13.5 }}>Pas encore assez de relevés sur cette période.</p> : <>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, fontSize: 12.5, color: 'var(--text-2)' }}>
          {lines.map((l) => <span key={l.key} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><span style={{ width: 14, height: 2, background: l.color }} />{l.label}</span>)}
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><span style={{ width: 14, borderTop: '1.5px dashed var(--warn)' }} />Seuil {warn} %</span>
        </div>
        <div style={{ position: 'relative' }}>
          <svg ref={svg} viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto', display: 'block', overflow: 'visible', touchAction: 'none' }}
            role="img" aria-label={`Évolution des niveaux d'encre sur ${days} jours`} onPointerMove={move} onPointerLeave={() => setHover(null)}>
            {[0, 25, 50, 75, 100].map((v) => <g key={v}><line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke={v ? 'var(--line-soft)' : 'var(--line-strong)'} />
              <text x={L - 8} y={y(v) + 4} textAnchor="end" fontSize="11.5" fill="var(--text-3)">{v ? `${v} %` : '0'}</text></g>)}
            <line x1={L} x2={W - R} y1={y(warn)} y2={y(warn)} stroke="var(--warn)" strokeWidth="1.5" strokeDasharray="5 4" />
            <line x1={L} x2={W - R} y1={y(crit)} y2={y(crit)} stroke="var(--crit)" strokeWidth="1.5" strokeDasharray="5 4" />
            {ticks.map((t, i) => <text key={t} x={x(t)} y={H - 6} fontSize="11.5" fill="var(--text-3)" textAnchor={i === 0 ? 'start' : i === 3 ? 'end' : 'middle'}>{fmtDay(t)}</text>)}
            {reps.map((r) => <g key={`${r.ts}-${r.label}`}><line x1={x(r.ts)} x2={x(r.ts)} y1={T} y2={H - B} stroke="var(--text-3)" strokeDasharray="2 3" />
              <title>{`${r.label} remplacée le ${fmtDate(r.ts)}`}</title></g>)}
            {lines.map((l) => <path key={l.key} d={path(l.pts)} fill="none" stroke={l.color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />)}
            {lines.map((l) => { const last = l.pts[l.pts.length - 1]; return <text key={l.key} x={W - R + 8} y={y(last[1]) + 4} fontSize="12" fill="var(--text)">{`${l.label.charAt(0)} ${Math.round(last[1])} %`}</text> })}
            {hover != null && <>
              <line x1={x(hover)} x2={x(hover)} y1={T} y2={H - B} stroke="var(--text-2)" />
              {at.map(({ l, v }) => <circle key={l.key} cx={x(hover)} cy={y(v!)} r="4" fill={l.color} stroke="var(--surface)" strokeWidth="2" />)}
            </>}
          </svg>
          {hover != null && at.length > 0 && (
            <div role="status" style={{ position: 'absolute', top: 4, left: `${Math.min(70, (x(hover) / W) * 100)}%`, transform: 'translateX(12px)', pointerEvents: 'none', background: 'var(--text)', color: 'var(--surface)', borderRadius: 6, padding: '8px 10px', fontSize: 12.5, minWidth: 150 }}>
              <div style={{ opacity: .75, marginBottom: 4 }}>{fmtDate(hover)}</div>
              {at.map(({ l, v }) => <div key={l.key} style={{ display: 'flex', alignItems: 'center', gap: 8 }}><span className="swatch" style={{ background: l.color }} />{l.label}<b style={{ marginLeft: 'auto', fontWeight: 600 }}>{Math.round(v!)} %</b></div>)}
            </div>
          )}
        </div>
        {reps.length > 0 && <p className="faint" style={{ margin: 0, fontSize: 12.5 }}>Lignes pointillées verticales : remplacement de cartouche détecté.</p>}
      </>}
    </div>
  )
}
