import { FormEvent, useEffect, useRef, useState } from 'react'

type ChatMsg = { id: number; from: 'customer' | 'bot' | 'agent'; author: string | null; body: string }
type Session = { session_id: string; token: string; name: string; email: string; greeting: string }
type Log = { ticket_ref: string | null; status: string | null; csat_token?: string; messages: ChatMsg[] }

const KEY = 'bp_chat_session'
const load = (): Session | null => { try { return JSON.parse(sessionStorage.getItem(KEY) || 'null') } catch { return null } }
const save = (s: Session | null) => { try { s ? sessionStorage.setItem(KEY, JSON.stringify(s)) : sessionStorage.removeItem(KEY) } catch { /* private mode */ } }

export default function ChatWidget() {
  const [session, setSession] = useState<Session | null>(load)
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [log, setLog] = useState<Log>({ ticket_ref: null, status: null, messages: [] })
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const [err, setErr] = useState('')
  const [rated, setRated] = useState<string | null>(null)
  const logRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!session) return
    const poll = () => fetch(`/api/chat/${session.session_id}/messages`, { headers: { 'X-Chat-Token': session.token } })
      .then(r => { if (r.status === 401) { save(null); setSession(null); throw new Error() } return r.json() }).then(setLog).catch(() => {})
    poll()
    const id = setInterval(poll, 4000)
    return () => clearInterval(id)
  }, [session])
  useEffect(() => { const el = logRef.current; if (el) el.scrollTop = el.scrollHeight }, [log.messages.length, sending])

  async function start(e: FormEvent) {
    e.preventDefault(); setErr('')
    const r = await fetch('/api/chat/sessions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, email }) })
    if (!r.ok) { setErr('Please enter your name and a valid email address.'); return }
    const s = await r.json(); save(s); setSession(s)
  }

  async function send(e: FormEvent) {
    e.preventDefault()
    if (!session || !text.trim()) return
    const body = text.trim(); setText(''); setSending(true); setErr('')
    setLog(l => ({ ...l, messages: [...l.messages, { id: -Date.now(), from: 'customer', author: session.name, body }] }))
    try {
      const r = await fetch(`/api/chat/${session.session_id}/messages`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Chat-Token': session.token }, body: JSON.stringify({ message: body }) })
      if (!r.ok) throw new Error()
      setLog(await r.json())
    } catch { setErr("Your message didn't send. Check your connection and try again."); setText(body) }
    finally { setSending(false) }
  }

  async function rate(score: number) {
    if (!log.ticket_ref || !log.csat_token) return
    await fetch(`/api/csat/${log.ticket_ref}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ score, token: log.csat_token }) })
    setRated(log.ticket_ref)
  }

  const answered = log.status === 'auto_resolved' || log.status === 'resolved'
  return (
    <div className="chat">
      <div className="chat-head">
        <span className="on" />
        <div><b>Beacon Lighting support</b><small>Instant answers 24/7, specialists 7 days</small></div>
        {session && <button className="btn ghost small" style={{ marginLeft: 'auto', color: '#fff' }} onClick={() => { save(null); setSession(null); setLog({ ticket_ref: null, status: null, messages: [] }) }}>New chat</button>}
      </div>
      {!session ? (
        <form className="chat-log" style={{ gap: 12 }} onSubmit={start}>
          <p style={{ margin: 0 }}>Hi! Tell us who you are so we can look up your orders.</p>
          <label className="field">Name<input className="input" required value={name} onChange={e => setName(e.target.value)} autoComplete="name" /></label>
          <label className="field">Email<input className="input" type="email" required value={email} onChange={e => setEmail(e.target.value)} autoComplete="email" /></label>
          {err && <div className="error">{err}</div>}
          <button className="btn primary" type="submit">Start chat</button>
        </form>
      ) : (
        <>
          <div className="chat-log" aria-live="polite" ref={logRef}>
            <div className="chat-msg bot"><span className="by">Beacon assistant</span>{session.greeting}</div>
            {log.messages.map(m => (
              <div key={m.id} className={`chat-msg ${m.from}`}>
                {m.from !== 'customer' && <span className="by">{m.from === 'agent' ? m.author : 'Beacon assistant'}</span>}{m.body}
              </div>
            ))}
            {sending && <div className="typing">Checking our systems…</div>}
            {answered && log.ticket_ref && rated !== log.ticket_ref && !sending && (
              <div className="chat-msg bot"><span className="by">Did that answer your question?</span>
                <div className="stars" role="group" aria-label="Rate this answer from 1 to 5">{[1, 2, 3, 4, 5].map(n => <button key={n} onClick={() => rate(n)}>{n}</button>)}</div>
              </div>
            )}
            {rated === log.ticket_ref && <div className="typing">Thanks for the rating.</div>}
          </div>
          {err && <div className="error" style={{ padding: '0 12px' }}>{err}</div>}
          <form className="chat-form" onSubmit={send}>
            <input className="input" placeholder="Ask about an order, stock, returns…" value={text} onChange={e => setText(e.target.value)} aria-label="Message" />
            <button className="btn lit" disabled={sending || !text.trim()} type="submit">Send</button>
          </form>
        </>
      )}
    </div>
  )
}
