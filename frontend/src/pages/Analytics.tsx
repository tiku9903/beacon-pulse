import { useEffect, useMemo, useState } from 'react'
import { api, hoursText, label, money } from '../api'

type Savings = { baseline_annual_cost: number; projected_annual_cost: number; annual_savings: number; cost_reduction: number; monthly_cost_of_delay: number; labour_cost_per_manual_ticket: number; fte_equivalent_now: number; assumptions: Record<string, number> }
type Overview = {
  window_days: number; tickets: number; resolved: number; open: number; safety_alerts: number
  resolution_mix: { auto: number; ai_assisted: number; manual: number }
  automation_rate: number | null; assisted_rate: number | null; avg_first_response_hours: number | null
  median_first_response_hours: number | null; sla_compliance: number | null; csat: number | null; csat_responses: number
  classification_accuracy: number | null; draft_acceptance_unedited: number | null
  after_hours_tickets: number; after_hours_answered_within_1h: number | null
  by_channel: Record<string, number>; by_intent: Record<string, { total: number; auto: number; ai_assisted: number; manual: number; corrected: number }>
  daily: { date: string; total: number; auto: number; ai_assisted: number; manual: number; avg_first_response_hours: number | null }[]
  baseline: { avg_first_response_hours: number; csat: number; annual_cost: number }
  targets: Record<string, number>; savings: Savings; savings_at_target: Savings
}

const C = { auto: '#1E8E5A', ai_assisted: '#D9A400', manual: '#3D5A99' }
const pct = (x: number | null | undefined) => x == null ? '—' : `${Math.round(x * 100)}%`

/** One metric on one line: where Beacon was, where it is now, where the business case says it should be. */
function Scale({ name, hint, now, nowText, baseline, baselineText, target, targetText, min, max, lowerIsBetter }: {
  name: string; hint: string; now: number | null; nowText: string; baseline?: number; baselineText?: string
  target: number; targetText: string; min: number; max: number; lowerIsBetter?: boolean
}) {
  const pos = (v: number) => { const p = ((v - min) / (max - min)) * 100; return Math.max(0, Math.min(100, lowerIsBetter ? 100 - p : p)) }
  const nowPos = now == null ? null : pos(now)
  return (
    <div className="scale">
      <div className="name"><b>{name}</b><span>{hint}</span></div>
      <div className="track" aria-label={`${name}: now ${nowText}, baseline ${baselineText}, target ${targetText}`}>
        <div className="bar" />
        {nowPos != null && <div className="fill" style={{ left: `${Math.min(pos(baseline ?? min), nowPos)}%`, width: `${Math.abs(nowPos - pos(baseline ?? min))}%` }} />}
        {baseline != null && <span className="pin" style={{ left: `${pos(baseline)}%` }}>Before {baselineText}</span>}
        <span className="pin target" style={{ left: `${pos(target)}%` }}>Target {targetText}</span>
        {nowPos != null && <span className="dot" style={{ left: `${nowPos}%` }} />}
      </div>
      <div className="now">{nowText}<small>now</small></div>
    </div>
  )
}

function MixChart({ data }: { data: Overview['daily'] }) {
  const [hover, setHover] = useState<number | null>(null)
  const W = 640, H = 220, pad = { l: 34, r: 8, t: 10, b: 24 }
  const max = Math.max(4, ...data.map(d => d.total))
  const step = Math.ceil(max / 4)
  const top = step * 4
  const bw = (W - pad.l - pad.r) / Math.max(data.length, 1)
  const y = (v: number) => pad.t + (H - pad.t - pad.b) * (1 - v / top)
  const keys = ['auto', 'ai_assisted', 'manual'] as const
  return (
    <div style={{ position: 'relative' }}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Daily tickets by how they were resolved" onMouseLeave={() => setHover(null)}>
        {[0, 1, 2, 3, 4].map(i => (
          <g key={i}>
            <line x1={pad.l} x2={W - pad.r} y1={y(i * step)} y2={y(i * step)} stroke="#ECEBE6" />
            <text x={pad.l - 6} y={y(i * step) + 4} fontSize="10" fill="#6B6E76" textAnchor="end">{i * step}</text>
          </g>
        ))}
        {data.map((d, i) => {
          let acc = 0
          const x = pad.l + i * bw + bw * 0.15
          const w = Math.max(2, bw * 0.7)
          return (
            <g key={d.date} onMouseEnter={() => setHover(i)}>
              <rect x={pad.l + i * bw} y={pad.t} width={bw} height={H - pad.t - pad.b} fill={hover === i ? 'rgba(0,0,0,.04)' : 'transparent'} />
              {keys.map(k => {
                const v = d[k]; if (!v) return null
                const y0 = y(acc), y1 = y(acc + v); acc += v
                return <rect key={k} x={x} y={y1 + 1} width={w} height={Math.max(0, y0 - y1 - 2)} rx={k === 'manual' || (k === 'ai_assisted' && !d.manual) ? 2 : 0} fill={C[k]} />
              })}
              {(i % Math.ceil(data.length / 8) === 0) && <text x={x + w / 2} y={H - 8} fontSize="10" fill="#6B6E76" textAnchor="middle">{d.date.slice(5).replace('-', '/')}</text>}
            </g>
          )
        })}
      </svg>
      {hover != null && data[hover] && (
        <div style={{ position: 'absolute', top: 0, left: `${Math.min(70, (hover / data.length) * 100)}%`, background: '#fff', border: '1px solid #E4E3DE', borderRadius: 6, padding: '8px 10px', fontSize: 12, pointerEvents: 'none', boxShadow: '0 4px 14px rgba(0,0,0,.08)' }}>
          <b>{new Date(data[hover].date).toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short' })}</b>
          <div className="num">{data[hover].total} tickets</div>
          {keys.map(k => <div key={k} className="num"><i style={{ display: 'inline-block', width: 8, height: 8, background: C[k], borderRadius: 2, marginRight: 5 }} />{label(k === 'auto' ? 'answered by AI' : k === 'ai_assisted' ? 'AI draft, agent sent' : 'agent wrote')}: {data[hover][k]}</div>)}
          <div className="muted">Avg first reply {hoursText(data[hover].avg_first_response_hours)}</div>
        </div>
      )}
    </div>
  )
}

export default function Analytics() {
  const [days, setDays] = useState(30)
  const [o, setO] = useState<Overview | null>(null)
  const [table, setTable] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => { setO(null); api<Overview>(`/api/analytics/overview?days=${days}`).then(setO).catch(e => setErr(e.message)) }, [days])
  const intents = useMemo(() => o ? Object.entries(o.by_intent).sort((a, b) => b[1].total - a[1].total) : [], [o])

  if (err) return <div className="page"><p className="error">{err}</p></div>
  if (!o) return <div className="page"><p className="muted">Loading performance…</p></div>
  const s = o.savings, t = o.savings_at_target

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Performance against the business case</h1>
          <p className="muted" style={{ margin: '4px 0 0' }}>{o.tickets.toLocaleString()} tickets in the last {o.window_days} days, {o.resolved.toLocaleString()} resolved, {o.safety_alerts} safety reports.</p>
        </div>
        <select className="input select-inline" value={days} onChange={e => setDays(Number(e.target.value))} aria-label="Time range">
          <option value={7}>Last 7 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option>
        </select>
      </div>

      <div className="scales">
        <Scale name="Answered by AI" hint="Resolved with no agent touch" now={o.automation_rate} nowText={pct(o.automation_rate)} baseline={0} baselineText="0%" target={o.targets.automation_rate} targetText={pct(o.targets.automation_rate)} min={0} max={1} />
        <Scale name="AI-drafted, agent sent" hint="Agent reviewed an AI draft" now={o.assisted_rate} nowText={pct(o.assisted_rate)} baseline={0} baselineText="0%" target={o.targets.assisted_rate} targetText={pct(o.targets.assisted_rate)} min={0} max={1} />
        <Scale name="Average first reply" hint={`Median ${hoursText(o.median_first_response_hours)}`} now={o.avg_first_response_hours} nowText={hoursText(o.avg_first_response_hours)} baseline={o.baseline.avg_first_response_hours} baselineText={hoursText(o.baseline.avg_first_response_hours)} target={o.targets.avg_first_response_hours} targetText={hoursText(o.targets.avg_first_response_hours)} min={0} max={13} lowerIsBetter />
        <Scale name="SLA met" hint="First reply inside the priority's window" now={o.sla_compliance} nowText={pct(o.sla_compliance)} target={o.targets.sla_compliance} targetText={pct(o.targets.sla_compliance)} min={0.5} max={1} />
        <Scale name="Intent accuracy" hint="Tickets agents didn't have to reclassify" now={o.classification_accuracy} nowText={pct(o.classification_accuracy)} target={o.targets.classification_accuracy} targetText={pct(o.targets.classification_accuracy)} min={0.5} max={1} />
        <Scale name="Customer satisfaction" hint={`${o.csat_responses} ratings`} now={o.csat} nowText={o.csat?.toFixed(1) ?? '—'} baseline={o.baseline.csat} baselineText={o.baseline.csat.toFixed(1)} target={o.targets.csat} targetText={o.targets.csat.toFixed(1)} min={1} max={5} />
      </div>

      <div className="two">
        <div className="panel">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12 }}>
            <h2>How tickets were resolved, per day</h2>
            <button className="btn ghost small" onClick={() => setTable(!table)}>{table ? 'Show chart' : 'Show as table'}</button>
          </div>
          <div className="legend">
            <span><i style={{ background: C.auto }} />Answered by AI ({o.resolution_mix.auto})</span>
            <span><i style={{ background: C.ai_assisted }} />AI draft, agent sent ({o.resolution_mix.ai_assisted})</span>
            <span><i style={{ background: C.manual }} />Agent wrote ({o.resolution_mix.manual})</span>
          </div>
          {table ? (
            <div style={{ maxHeight: 260, overflow: 'auto' }}>
              <table className="grid"><thead><tr><th>Date</th><th className="r">Total</th><th className="r">AI</th><th className="r">Drafted</th><th className="r">Agent</th><th className="r">Avg first reply</th></tr></thead>
                <tbody>{o.daily.map(d => <tr key={d.date}><td>{d.date}</td><td className="r num">{d.total}</td><td className="r num">{d.auto}</td><td className="r num">{d.ai_assisted}</td><td className="r num">{d.manual}</td><td className="r num">{hoursText(d.avg_first_response_hours)}</td></tr>)}</tbody></table>
            </div>
          ) : <MixChart data={o.daily} />}
          <p className="small muted" style={{ marginBottom: 0 }}>
            {o.after_hours_tickets} tickets arrived outside Melbourne business hours; {pct(o.after_hours_answered_within_1h)} of them got a reply within an hour.
            {o.draft_acceptance_unedited != null && ` Agents sent ${pct(o.draft_acceptance_unedited)} of AI drafts without edits.`}
          </p>
        </div>

        <div className="panel">
          <h2>Annual cost at today's automation mix</h2>
          <p className="small muted" style={{ marginTop: 2 }}>Projected from this period's resolution mix against the {money(s.baseline_annual_cost)} baseline.</p>
          <div className="money-big">{money(s.projected_annual_cost)}</div>
          <p style={{ margin: '6px 0 14px' }}><b className="num">{money(s.annual_savings)}</b> saved a year, a {pct(s.cost_reduction)} reduction, about {s.fte_equivalent_now} full-time agents' worth of handling.</p>
          <dl className="kv" style={{ gridTemplateColumns: '1fr auto' }}>
            <dt>At target mix (60% / 30%)</dt><dd className="num">{money(t.projected_annual_cost)}</dd>
            <dt>Savings at target</dt><dd className="num">{money(t.annual_savings)}</dd>
            <dt>Cost of each month's delay now</dt><dd className="num">{money(s.monthly_cost_of_delay)}</dd>
            <dt>Labour per manual ticket</dt><dd className="num">{money(s.labour_cost_per_manual_ticket)}</dd>
            <dt>Agent effort on a drafted ticket</dt><dd className="num">{pct(s.assumptions.assisted_effort_ratio)}</dd>
            <dt>Platform cost (AI, hosting)</dt><dd className="num">{money(s.assumptions.platform_annual_cost)}</dd>
          </dl>
        </div>
      </div>

      <div className="panel" style={{ marginTop: 20 }}>
        <h2>By resolution path</h2>
        <p className="small muted" style={{ marginTop: 2 }}>Which of the eight paths the AI handles alone, and where agents correct it. Expand automation path by path from here.</p>
        <table className="grid">
          <thead><tr><th>Path</th><th className="r">Tickets</th><th className="r">Answered by AI</th><th className="r">AI-drafted</th><th className="r">Agent wrote</th><th className="r">Reclassified</th></tr></thead>
          <tbody>
            {intents.map(([k, v]) => (
              <tr key={k}><td>{label(k)}</td><td className="r num">{v.total}</td><td className="r num">{v.total ? pct(v.auto / v.total) : '—'}</td><td className="r num">{v.total ? pct(v.ai_assisted / v.total) : '—'}</td><td className="r num">{v.total ? pct(v.manual / v.total) : '—'}</td><td className="r num">{v.corrected}</td></tr>
            ))}
          </tbody>
        </table>
        <p className="small muted" style={{ marginBottom: 0 }}>Channels: {Object.entries(o.by_channel).map(([k, v]) => `${label(k)} ${v}`).join(', ')}.</p>
      </div>
    </div>
  )
}
