import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ago, api, TicketSummary } from '../api'
import { channelText, Filament, Tags } from '../components/Bits'
import TicketView from '../components/TicketView'
import { useSession } from '../main'

type View = 'queue' | 'mine' | 'safety'
type Filter = { key: string; label: string; query: string }

const BASE: Record<View, Filter[]> = {
  queue: [
    { key: 'open', label: 'Open', query: 'open_only=true' },
    { key: 'customer_support', label: 'Support', query: 'open_only=true&team=customer_support' },
    { key: 'trade', label: 'Trade', query: 'open_only=true&team=trade' },
    { key: 'returns', label: 'Returns', query: 'open_only=true&team=returns' },
    { key: 'warranty', label: 'Warranty', query: 'open_only=true&team=warranty' },
    { key: 'technical', label: 'Technical', query: 'open_only=true&team=technical' },
    { key: 'auto', label: 'Answered by AI', query: 'status=auto_resolved' },
    { key: 'waiting', label: 'Waiting on customer', query: 'status=awaiting_customer' },
    { key: 'done', label: 'Resolved', query: 'status=resolved,closed' },
  ],
  mine: [
    { key: 'open', label: 'Open', query: 'mine=true&open_only=true' },
    { key: 'waiting', label: 'Waiting on customer', query: 'mine=true&status=awaiting_customer' },
    { key: 'done', label: 'Resolved', query: 'mine=true&status=resolved,closed' },
  ],
  safety: [
    { key: 'open', label: 'Open', query: 'safety=true&open_only=true' },
    { key: 'all', label: 'All safety reports', query: 'safety=true' },
  ],
}

export default function Inbox({ view }: { view: View }) {
  const { refreshCounts } = useSession()
  const [params, setParams] = useSearchParams()
  const filters = BASE[view]
  const fkey = params.get('f') ?? filters[0].key
  const filter = filters.find(f => f.key === fkey) ?? filters[0]
  const selected = Number(params.get('t')) || null
  const [q, setQ] = useState('')
  const [items, setItems] = useState<TicketSummary[] | null>(null)
  const [total, setTotal] = useState(0)

  const load = useCallback(() => {
    const qs = filter.query + (q ? `&q=${encodeURIComponent(q)}` : '')
    api<{ total: number; items: TicketSummary[] }>(`/api/tickets?${qs}&limit=200`)
      .then(r => { setItems(r.items); setTotal(r.total) }).catch(() => setItems([]))
  }, [filter.query, q])

  useEffect(() => { setItems(null); load() }, [load])
  useEffect(() => { const id = setInterval(load, 15000); return () => clearInterval(id) }, [load])

  const select = (id: number | null) => {
    const next = new URLSearchParams(params)
    if (id) next.set('t', String(id)); else next.delete('t')
    setParams(next)
  }
  const setFilter = (k: string) => { const next = new URLSearchParams(params); next.set('f', k); next.delete('t'); setParams(next) }

  return (
    <div className={`inbox ${selected ? 'has-selection' : ''}`}>
      <div className="queue">
        <div className="queue-tools">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
            <h1>{view === 'queue' ? 'Queue' : view === 'mine' ? 'Assigned to me' : 'Safety alerts'}</h1>
            <span className="muted small num">{total} tickets</span>
          </div>
          <input className="input" placeholder="Search by name, email, ref or text" value={q} onChange={e => setQ(e.target.value)} aria-label="Search tickets" />
          <div className="tabs" role="tablist">
            {filters.map(f => <button key={f.key} role="tab" aria-selected={f.key === filter.key} className={`tab ${f.key === filter.key ? 'on' : ''}`} onClick={() => setFilter(f.key)}>{f.label}</button>)}
          </div>
        </div>
        <div className="queue-list">
          {items === null && <div className="empty">Loading tickets…</div>}
          {items?.length === 0 && <div className="empty"><div><b>Nothing here</b>{view === 'safety' ? 'No open safety reports.' : 'New enquiries appear here as soon as they arrive.'}</div></div>}
          {items?.map(t => (
            <button key={t.id} className={`row ${selected === t.id ? 'selected' : ''}`} onClick={() => select(t.id)}>
              <span className={`lane ${t.priority}`} />
              <span className="row-body">
                <span className="row-top">
                  <span className="who">{t.contact_name ?? t.contact_email ?? 'Unknown customer'}</span>
                  <span className="when">{ago(t.created_at)} via {channelText[t.channel] ?? t.channel}</span>
                </span>
                <span className="preview">{t.subject && t.channel !== 'chat' ? t.subject : t.preview}</span>
                <span className="row-meta"><Tags t={t} />{t.assigned_agent && <span className="muted">{t.assigned_agent.name}</span>}</span>
                <Filament t={t} />
              </span>
            </button>
          ))}
        </div>
      </div>
      {selected
        ? <TicketView id={selected} onBack={() => select(null)} onChange={() => { load(); refreshCounts() }} />
        : <div className="workspace blank"><div className="empty"><div><b>Pick a ticket from the queue</b>Highest priority and nearest deadline are at the top.</div></div></div>}
    </div>
  )
}
