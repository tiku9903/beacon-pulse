import { useEffect, useState } from 'react'
import { api, label } from '../api'
import { useSession } from '../main'

type Config = {
  environment: string; ai_provider_configured: string; ai_provider_active: string; ai_models: Record<string, string[]>
  automation_mode: string; auto_resolve_intents: string[]; auto_resolve_min_confidence: number; refund_auto_approve_limit: number
  connectors: Record<string, string>; email_transport: string; sms_transport: string; knowledge_passages: number
}
type Passage = { id: string; doc: string; title: string; text: string }
type Sent = { kind: string; to: string; subject: string | null; body: string }

export default function Settings() {
  const { agent } = useSession()
  const [cfg, setCfg] = useState<Config | null>(null)
  const [kb, setKb] = useState<Passage[]>([])
  const [outbox, setOutbox] = useState<Sent[]>([])
  const [msg, setMsg] = useState('')
  const load = () => {
    api<Config>('/api/admin/config').then(setCfg).catch(() => {})
    api<Passage[]>('/api/admin/knowledge').then(setKb).catch(() => {})
    api<Sent[]>('/api/admin/outbox').then(setOutbox).catch(() => {})
  }
  useEffect(load, [])

  async function post(path: string, done: (r: Record<string, number>) => string) {
    try { setMsg(done(await api<Record<string, number>>(path, { method: 'POST' }))); load() }
    catch (e) { setMsg(e instanceof Error ? e.message : 'Action failed.') }
  }

  if (!cfg) return <div className="page"><p className="muted">Loading settings…</p></div>
  const live = cfg.ai_provider_active !== 'rules'
  return (
    <div className="page">
      <div className="page-head"><div><h1>Settings</h1><p className="muted" style={{ margin: '4px 0 0' }}>Set through environment variables on the server (see the deployment guide). Shown here so the team can see what's switched on.</p></div></div>

      <div className="two" style={{ marginTop: 0 }}>
        <div className="panel">
          <h2>Automation</h2>
          <dl className="kv" style={{ gridTemplateColumns: '180px 1fr', marginTop: 10 }}>
            <dt>Mode</dt><dd><b>{cfg.automation_mode === 'live' ? 'Live: AI sends answers' : 'Shadow: AI drafts only, sends nothing'}</b></dd>
            <dt>Paths answered by AI</dt><dd>{cfg.auto_resolve_intents.map(label).join(', ') || 'None'}</dd>
            <dt>Minimum confidence</dt><dd className="num">{Math.round(cfg.auto_resolve_min_confidence * 100)}%</dd>
            <dt>Refund auto-approval limit</dt><dd className="num">{cfg.refund_auto_approve_limit ? `$${cfg.refund_auto_approve_limit}` : 'Off: every refund needs a supervisor'}</dd>
            <dt>AI engine</dt><dd>{live ? `${label(cfg.ai_provider_active)} (${cfg.ai_models[cfg.ai_provider_active]?.join(', ')})` : `Rules engine${cfg.ai_provider_configured !== 'rules' ? ` (${cfg.ai_provider_configured} key not set)` : ''}`}</dd>
          </dl>
          {!live && <p className="small" style={{ background: 'var(--beacon-soft)', padding: '8px 10px', borderRadius: 6 }}>Running on the built-in rules engine. Set <code>GROQ_API_KEY</code> (free) for AI classification and drafting.</p>}
        </div>
        <div className="panel">
          <h2>Connected systems</h2>
          <dl className="kv" style={{ gridTemplateColumns: '140px 1fr', marginTop: 10 }}>
            <dt>Orders</dt><dd>{cfg.connectors.orders}</dd>
            <dt>Customer 360</dt><dd>{cfg.connectors.crm}</dd>
            <dt>Live stock</dt><dd>{cfg.connectors.inventory}</dd>
            <dt>Email out</dt><dd>{cfg.email_transport}</dd>
            <dt>SMS out</dt><dd>{cfg.sms_transport}</dd>
            <dt>Environment</dt><dd>{cfg.environment}</dd>
          </dl>
          {agent?.role === 'admin' && (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
              <button className="btn" onClick={() => post('/api/admin/knowledge/reload', r => `Reloaded ${r.passages} policy passages.`)}>Reload policy knowledge base</button>
              <button className="btn danger" onClick={() => { if (confirm('Delete all demo tickets? This cannot be undone.')) post('/api/admin/demo-data/purge', r => `Deleted ${r.deleted_tickets} demo tickets.`) }}>Delete demo tickets</button>
            </div>
          )}
          {msg && <p className="small">{msg}</p>}
        </div>
      </div>

      <div className="panel" style={{ marginTop: 20 }}>
        <h2>Policy knowledge base</h2>
        <p className="small muted" style={{ marginTop: 2 }}>{cfg.knowledge_passages} passages. AI answers and drafts may only use these. Replace the placeholder wording with Beacon's approved policy before go-live.</p>
        <table className="grid"><thead><tr><th style={{ width: 220 }}>Section</th><th>Text</th></tr></thead>
          <tbody>{kb.map(p => <tr key={p.id}><td><b>{p.title}</b><div className="small muted">{p.doc}</div></td><td className="small">{p.text}</td></tr>)}</tbody></table>
      </div>

      {cfg.email_transport === 'console' && (
        <div className="panel" style={{ marginTop: 20 }}>
          <h2>Test outbox</h2>
          <p className="small muted" style={{ marginTop: 2 }}>Email and SMS aren't connected yet, so messages that would have gone to customers are kept here.</p>
          {outbox.length === 0 ? <p className="muted">Nothing sent since the server started.</p> : (
            <table className="grid"><thead><tr><th>To</th><th>Message</th></tr></thead>
              <tbody>{outbox.slice(0, 30).map((m, i) => <tr key={i}><td className="small">{label(m.kind)}<div className="muted">{m.to}</div></td><td className="small" style={{ whiteSpace: 'pre-wrap' }}>{m.subject && <b>{m.subject}{'\n'}</b>}{m.body}</td></tr>)}</tbody></table>
          )}
        </div>
      )}
    </div>
  )
}
