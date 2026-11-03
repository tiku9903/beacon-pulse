import { useEffect, useState } from 'react'
import { label, TicketSummary } from '../api'

export const Bolt = ({ size = 16 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true"><path d="M13.5 2 4 14h6.5L9 22l11-13h-6.5z" fill="#000" /></svg>
)

const SLA_HOURS: Record<string, number> = { P1: 0.5, P2: 2, P3: 8, P4: 24 }

/** The SLA filament: burns down from full to empty as the first-response deadline approaches. */
export function Filament({ t }: { t: TicketSummary }) {
  const [, tick] = useState(0)
  useEffect(() => { const id = setInterval(() => tick(x => x + 1), 30000); return () => clearInterval(id) }, [])
  if (!t.sla_due_at || t.first_response_at || !['new', 'awaiting_agent', 'awaiting_approval'].includes(t.status)) return null
  const due = new Date(t.sla_due_at).getTime()
  const total = SLA_HOURS[t.priority] * 3600_000
  const left = due - Date.now()
  const pct = Math.max(0, Math.min(100, (left / total) * 100))
  const out = left <= 0
  const mins = Math.abs(Math.round(left / 60000))
  const text = mins >= 120 ? `${Math.round(mins / 60)} h` : `${mins} min`
  return (
    <div>
      <div className={`filament ${out ? 'out' : pct < 25 ? 'hot' : ''}`} role="presentation"><i style={{ width: `${pct}%` }} /></div>
      <span className={`sla-text num ${out ? 'out' : ''}`}>{out ? `First reply overdue by ${text}` : `First reply due in ${text}`}</span>
    </div>
  )
}

export function Tags({ t }: { t: TicketSummary }) {
  return (
    <>
      <span className={`tag ${t.priority}`}>{t.priority}</span>
      {t.safety_flag && <span className="tag safety">Safety</span>}
      {t.customer_segment === 'trade' && <span className="tag trade">Trade{t.trade_tier ? ` ${t.trade_tier}` : ''}</span>}
      {t.intent && <span className="tag">{label(t.intent)}</span>}
      {(t.sentiment === 'angry' || t.sentiment === 'negative') && <span className={`tag ${t.sentiment}`}>{label(t.sentiment)}</span>}
      {t.pending_approvals > 0 && <span className="tag approval">Needs approval</span>}
      {t.resolution_type === 'auto' && <span className="tag auto">Auto-resolved</span>}
    </>
  )
}

export const statusText: Record<string, string> = {
  new: 'Processing', auto_resolved: 'Auto-resolved', awaiting_agent: 'Waiting for agent',
  awaiting_approval: 'Waiting for approval', awaiting_customer: 'Waiting for customer', resolved: 'Resolved', closed: 'Closed',
}

export const channelText: Record<string, string> = { email: 'Email', web_form: 'Web form', chat: 'Live chat', sms: 'SMS', api: 'API' }
