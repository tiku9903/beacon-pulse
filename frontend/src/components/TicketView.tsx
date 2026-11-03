import { Fragment, useCallback, useEffect, useState } from 'react'
import { Agent, ago, api, Approval, label, money, TicketDetail } from '../api'
import { useSession } from '../main'
import { channelText, Filament, statusText, Tags } from './Bits'

const EVENT_TEXT: Record<string, (d: Record<string, unknown>) => string> = {
  intake: d => `Received via ${channelText[String(d.channel)] ?? d.channel}`,
  identify: d => d.result === 'matched' ? `Matched CRM record ${d.crm_id} (${d.segment}${d.tier ? `, ${d.tier}` : ''})` : 'No CRM match - new customer',
  classified: d => `Classified as ${label(String(d.intent))} (${Math.round(Number(d.confidence) * 100)}%)${d.safety ? ', safety risk' : ''}`,
  routed: d => `Routed to ${label(String(d.team))} as ${d.priority}`,
  assigned: d => `Assigned to ${d.agent}`,
  auto_resolved: () => 'Answered automatically',
  auto_clarification: () => 'Asked customer for missing details',
  shadow_auto_resolution: () => 'Shadow mode: AI answer held for review',
  human_handoff: () => 'Customer replied after AI answer - handed to a person',
  approval_requested: d => `Requested ${d.action} approval${d.amount ? ` for ${money(Number(d.amount))}` : ''}`,
  approval_approved: d => `${label(String(d.action))} approved`,
  approval_rejected: d => `${label(String(d.action))} rejected`,
  reply_sent: d => d.author_type === 'system' ? 'Acknowledgement sent' : 'Reply sent',
  agent_reply: d => d.used_draft ? (d.draft_edited ? 'Agent sent edited AI draft' : 'Agent sent AI draft unchanged') : 'Agent wrote reply',
  reclassified: d => `Reclassified ${label(String(d.from_intent))} to ${label(String(d.to_intent))}`,
  customer_follow_up: () => 'Customer followed up',
  pipeline_complete: d => `Pipeline finished in ${d.total_ms} ms`,
  csat: d => `Customer rated ${d.score}/5`,
  status_changed: d => `Status set to ${label(String(d.status))}`,
  reassigned: () => 'Reassigned',
  redrafted: () => 'Draft regenerated',
  safety_flag_changed: d => d.safety_flag ? 'Safety flag raised' : `Safety flag cleared: ${d.note}`,
  pipeline_error: () => 'Automation failed - routed to a person',
}

function clock(iso: string) { return new Date(iso).toLocaleTimeString('en-AU', { hour: '2-digit', minute: '2-digit' }) }

export default function TicketView({ id, onBack, onChange }: { id: number; onBack: () => void; onChange: () => void }) {
  const { meta, agent } = useSession()
  const [t, setT] = useState<TicketDetail | null>(null)
  const [agents, setAgents] = useState<Agent[]>([])
  const [text, setText] = useState('')
  const [draftId, setDraftId] = useState<number | null>(null)
  const [note, setNote] = useState(false)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')

  const apply = useCallback((d: TicketDetail) => {
    setT(d)
    const pending = [...d.drafts].reverse().find(x => x.status === 'pending')
    setDraftId(pending?.id ?? null)
    setText(pending?.body ?? '')
  }, [])

  useEffect(() => {
    setT(null); setErr(''); setNote(false)
    api<TicketDetail>(`/api/tickets/${id}`).then(apply).catch(e => setErr(e.message))
  }, [id, apply])
  useEffect(() => { api<Agent[]>('/api/agents').then(setAgents).catch(() => {}) }, [])

  async function act(name: string, path: string, json?: unknown) {
    setBusy(name); setErr('')
    try { apply(await api<TicketDetail>(path, { method: 'POST', json: json ?? {} })); onChange() }
    catch (e) { setErr(e instanceof Error ? e.message : 'That action failed. Try again.') }
    finally { setBusy('') }
  }

  async function decide(a: Approval, decision: 'approve' | 'reject') {
    setBusy(`ap${a.id}`); setErr('')
    try {
      await api(`/api/approvals/${a.id}/decide`, { method: 'POST', json: { decision } })
      apply(await api<TicketDetail>(`/api/tickets/${id}`)); onChange()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Approval failed.') }
    finally { setBusy('') }
  }

  if (err && !t) return <div className="workspace"><div className="empty"><div><b>Couldn't open this ticket</b>{err}</div></div></div>
  if (!t) return <div className="workspace"><div className="empty">Loading ticket…</div></div>

  const open = ['new', 'awaiting_agent', 'awaiting_approval', 'awaiting_customer'].includes(t.status)
  const draft = t.drafts.find(d => d.id === draftId)
  const ents = Object.entries(t.entities || {}).filter(([, v]) => v !== null && v !== '')

  return (
    <div className="workspace">
      <div className="convo">
        <div className="convo-head">
          <div className="line">
            <button className="btn ghost small" onClick={onBack} aria-label="Back to queue">Back</button>
            <span className="muted num">{t.ref}</span>
            <span className="muted">{statusText[t.status] ?? t.status}</span>
          </div>
          <h1>{t.subject && t.channel !== 'chat' ? t.subject : `${channelText[t.channel]} with ${t.contact_name ?? 'customer'}`}</h1>
          <div className="line"><Tags t={t} /></div>
          <div style={{ maxWidth: 320 }}><Filament t={t} /></div>
        </div>

        {t.messages.map(m => (
          <div key={m.id} className={`msg ${m.direction} ${m.author_type === 'ai' || m.author_type === 'system' ? 'ai' : ''}`}>
            <div className="meta">
              {m.direction === 'internal' ? 'Internal note by ' : ''}{m.author_name ?? (m.direction === 'inbound' ? 'Customer' : 'Beacon')}
              {m.author_type === 'ai' && ' (sent automatically)'}, {ago(m.created_at)}
            </div>
            <div className="bubble">{m.body}</div>
          </div>
        ))}

        {t.approvals.map(a => (
          <div key={a.id} className={`approval-box ${a.status !== 'pending' ? 'done' : ''}`}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
              <h3>{label(a.action_type)} {a.amount ? money(a.amount) : ''} {a.payload.order_id ? `for ${a.payload.order_id}` : ''}</h3>
              <span className="small">{a.status === 'pending' ? 'Waiting for sign-off' : `${label(a.status)} by ${a.decided_by ?? 'policy'}`}</span>
            </div>
            {a.ai_recommendation && <div className="small">{a.ai_recommendation}</div>}
            {a.status === 'pending' && (
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn primary" disabled={!!busy} onClick={() => decide(a, 'approve')}>Approve and notify customer</button>
                <button className="btn danger" disabled={!!busy} onClick={() => decide(a, 'reject')}>Reject</button>
                {a.action_type === 'refund' && agent?.role === 'agent' && <span className="small muted" style={{ alignSelf: 'center' }}>Refunds need a supervisor</span>}
              </div>
            )}
          </div>
        ))}

        <div className="composer">
          <div className="composer-head">
            {note ? 'Internal note (not sent to customer)' : draft ? 'AI draft, review before sending' : `Reply by ${channelText[t.channel]?.toLowerCase() ?? t.channel}`}
            {draft && draft.kb_sources.length > 0 && !note && <span className="src">Grounded in: {draft.kb_sources.map(s => s.title).join(', ')}</span>}
          </div>
          <textarea value={text} onChange={e => setText(e.target.value)} aria-label="Reply text" placeholder={note ? 'Write a note for the team' : 'Write a reply'} />
          <div className="composer-actions">
            {note ? (
              <button className="btn primary" disabled={!text.trim() || !!busy} onClick={() => act('note', `/api/tickets/${id}/note`, { body: text }).then(() => { setNote(false) })}>Add note</button>
            ) : (<>
              <button className="btn lit" disabled={!text.trim() || !!busy} onClick={() => act('send', `/api/tickets/${id}/reply`, { body: text, draft_id: draftId, resolve: true })}>{busy === 'send' ? 'Sending…' : 'Send and resolve'}</button>
              <button className="btn" disabled={!text.trim() || !!busy} onClick={() => act('send2', `/api/tickets/${id}/reply`, { body: text, draft_id: draftId, resolve: false })}>Send and wait for reply</button>
            </>)}
            <span className="spacer" />
            {!note && <button className="btn ghost" disabled={!!busy} onClick={() => act('redraft', `/api/tickets/${id}/redraft`)}>{busy === 'redraft' ? 'Drafting…' : 'Redraft'}</button>}
            <button className="btn ghost" onClick={() => { setNote(!note); setText(note ? (draft?.body ?? '') : '') }}>{note ? 'Back to reply' : 'Write note'}</button>
            {open && <button className="btn ghost" disabled={!!busy} onClick={() => act('close', `/api/tickets/${id}/status`, { status: 'resolved' })}>Resolve without reply</button>}
          </div>
        </div>
        {err && <p className="error" role="alert">{err}</p>}
      </div>

      <aside className="side">
        <section>
          <h2>Customer</h2>
          {t.customer ? (
            <dl className="kv">
              <dt>Name</dt><dd>{t.customer.name}</dd>
              <dt>Segment</dt><dd>{t.customer.segment === 'trade' ? `Trade, ${t.customer.trade_tier ?? 'standard'}` : 'Retail'}</dd>
              <dt>Lifetime</dt><dd className="num">{money(t.customer.lifetime_value)}</dd>
              <dt>Email</dt><dd>{t.customer.email}</dd>
              <dt>Phone</dt><dd>{t.customer.phone}</dd>
              <dt>CRM</dt><dd>{t.customer.crm_id}</dd>
            </dl>
          ) : (
            <dl className="kv"><dt>Name</dt><dd>{t.contact_name ?? '—'}</dd><dt>Email</dt><dd>{t.contact_email ?? '—'}</dd><dt>CRM</dt><dd className="muted">No matching record</dd></dl>
          )}
        </section>

        <section>
          <h2>AI triage</h2>
          <label className="field small">Intent
            <select className="input" value={t.intent ?? ''} disabled={!!busy}
              onChange={e => act('reclass', `/api/tickets/${id}/reclassify`, { intent: e.target.value })}>
              {Object.keys(meta?.intents ?? {}).map(k => <option key={k} value={k}>{label(k)}</option>)}
            </select>
          </label>
          {t.agent_corrected_intent ? <span className="small muted">Corrected by an agent, saved as training feedback</span> : (
            <div>
              <div className="conf"><i style={{ width: `${Math.round((t.intent_confidence ?? 0) * 100)}%` }} /></div>
              <span className="small muted num">{Math.round((t.intent_confidence ?? 0) * 100)}% confidence, {t.ai_provider}</span>
            </div>
          )}
          <dl className="kv">
            <dt>Sentiment</dt><dd>{label(t.sentiment)}</dd>
            <dt>Urgency</dt><dd className="num">{t.urgency}/100</dd>
            {t.safety_flag && (<><dt>Safety</dt><dd style={{ color: 'var(--signal)', fontWeight: 700 }}>{t.safety_reasons.map(label).join(', ') || 'Flagged'}</dd></>)}
            {ents.map(([k, v]) => <Fragment key={k}><dt>{label(k)}</dt><dd>{String(v)}</dd></Fragment>)}
          </dl>
          {t.classification_reasoning && <p className="small muted" style={{ margin: 0 }}>{t.classification_reasoning}</p>}
          {t.safety_flag && agent && agent.role !== 'agent' && (
            <button className="btn ghost small" style={{ justifySelf: 'start' }} disabled={!!busy} onClick={() => {
              const note = prompt('Why is this not a safety issue? (kept in the audit log)')
              if (note && note.trim().length >= 3) act('safety', `/api/tickets/${id}/safety`, { safety_flag: false, note })
            }}>Clear safety flag</button>
          )}
        </section>

        <section>
          <h2>Ownership</h2>
          <label className="field small">Team
            <select className="input" value={t.team ?? ''} onChange={e => act('team', `/api/tickets/${id}/assign`, { team: e.target.value, agent_id: t.assigned_agent?.id ?? null })}>
              {Object.entries(meta?.teams ?? {}).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
          <label className="field small">Agent
            <select className="input" value={t.assigned_agent?.id ?? ''} onChange={e => act('assign', `/api/tickets/${id}/assign`, { agent_id: e.target.value ? Number(e.target.value) : null })}>
              <option value="">Unassigned</option>
              {agents.filter(a => a.active).map(a => <option key={a.id} value={a.id}>{a.name} ({a.open_tickets ?? 0} open)</option>)}
            </select>
          </label>
        </section>

        <section>
          <h2>Timeline</h2>
          <ul className="timeline">
            {t.events.filter(e => EVENT_TEXT[e.event]).map(e => (
              <li key={e.id}><span className="t">{clock(e.created_at)}</span><span>{EVENT_TEXT[e.event](e.data)}</span></li>
            ))}
          </ul>
        </section>
      </aside>
    </div>
  )
}
