import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ago, api, Approval, label, money } from '../api'
import { useSession } from '../main'

export default function Approvals() {
  const { agent, refreshCounts } = useSession()
  const [tab, setTab] = useState<'pending' | 'approved' | 'rejected'>('pending')
  const [rows, setRows] = useState<Approval[] | null>(null)
  const [busy, setBusy] = useState<number | null>(null)
  const [err, setErr] = useState('')

  const load = useCallback(() => { api<Approval[]>(`/api/approvals?status=${tab}`).then(setRows).catch(() => setRows([])) }, [tab])
  useEffect(() => { setRows(null); load() }, [load])

  async function decide(a: Approval, decision: 'approve' | 'reject') {
    setBusy(a.id); setErr('')
    try { await api(`/api/approvals/${a.id}/decide`, { method: 'POST', json: { decision } }); load(); refreshCounts() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Decision failed.') }
    finally { setBusy(null) }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Approvals</h1>
          <p className="muted" style={{ margin: '4px 0 0' }}>Refunds, returns and warranty replacements the AI recommended. Nothing moves money until a person signs off. Refunds need a supervisor.</p>
        </div>
        <div className="tabs">
          {(['pending', 'approved', 'rejected'] as const).map(k => <button key={k} className={`tab ${tab === k ? 'on' : ''}`} onClick={() => setTab(k)}>{label(k)}</button>)}
        </div>
      </div>
      {err && <p className="error" role="alert">{err}</p>}
      {rows === null ? <p className="muted">Loading…</p> : rows.length === 0 ? (
        <div className="panel"><b>No {tab} approvals.</b> <span className="muted">{tab === 'pending' ? 'New requests appear here when a ticket needs a refund, return or replacement.' : ''}</span></div>
      ) : (
        <table className="grid">
          <thead><tr><th>Ticket</th><th>Action</th><th className="r">Amount</th><th>AI recommendation</th><th>Requested</th><th>{tab === 'pending' ? '' : 'Decision'}</th></tr></thead>
          <tbody>
            {rows.map(a => (
              <tr key={a.id}>
                <td><Link to={`/?t=${a.ticket_id}`}>{a.ticket_ref}</Link><div className="small muted">{a.contact_name}</div></td>
                <td>{label(a.action_type)}{a.payload.order_id ? <div className="small muted">{String(a.payload.order_id)}</div> : null}</td>
                <td className="r num">{money(a.amount)}</td>
                <td className="small" style={{ maxWidth: 380 }}>{a.ai_recommendation}</td>
                <td className="small muted">{ago(a.created_at)}</td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  {tab === 'pending' ? (
                    a.action_type === 'refund' && agent?.role === 'agent'
                      ? <span className="small muted">Supervisor sign-off</span>
                      : <span style={{ display: 'flex', gap: 6 }}>
                          <button className="btn primary" disabled={busy === a.id} onClick={() => decide(a, 'approve')}>Approve</button>
                          <button className="btn danger" disabled={busy === a.id} onClick={() => decide(a, 'reject')}>Reject</button>
                        </span>
                  ) : <span className="small">{a.decided_by ?? 'Policy'}{a.decision_note ? `: ${a.decision_note}` : ''}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
