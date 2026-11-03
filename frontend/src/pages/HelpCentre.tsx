import { FormEvent, useState } from 'react'
import ChatWidget from '../components/ChatWidget'
import { Bolt } from '../components/Bits'

/** Customer-facing contact page. The same form posts to /api/channels/web-form, so it can be
 *  rebuilt natively in Magento; the chat embeds anywhere via /widget.js. */
export default function HelpCentre() {
  const [f, setF] = useState({ name: '', email: '', phone: '', order_number: '', subject: '', message: '' })
  const [done, setDone] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setErr('')
    try {
      const body = Object.fromEntries(Object.entries(f).filter(([, v]) => v.trim()))
      const r = await fetch('/api/channels/web-form', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      if (!r.ok) throw new Error()
      setDone((await r.json()).ref)
    } catch { setErr('We couldn’t send your message. Check the email address and try again.') }
    finally { setBusy(false) }
  }
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value })

  return (
    <div className="help">
      <div className="help-top"><span style={{ background: 'var(--beacon)', borderRadius: 6, width: 28, height: 28, display: 'grid', placeItems: 'center' }}><Bolt /></span><b>BEACON LIGHTING</b><span style={{ color: '#A7A59D' }}>Help & contact</span></div>
      <div className="help-hero">
        <h1>How can we help with your lighting?</h1>
        <p>Order updates, stock checks and store questions are answered straight away, day or night. Returns, warranty and trade enquiries go to the right specialist with your details already attached.</p>
      </div>
      <div className="help-grid">
        <div className="help-card" style={{ padding: 0, overflow: 'hidden', minHeight: 520 }}><ChatWidget /></div>
        <form className="help-card" onSubmit={submit}>
          <h2>Send us a message</h2>
          {done ? (
            <div className="thanks" role="status"><b>Thanks, we've got it.</b><br />Your reference is <b>{done}</b>. We've emailed you a confirmation, and often the answer too.</div>
          ) : (<>
            <label className="field">Name<input className="input" required value={f.name} onChange={set('name')} autoComplete="name" /></label>
            <label className="field">Email<input className="input" type="email" required value={f.email} onChange={set('email')} autoComplete="email" /></label>
            <label className="field">Order number (if you have one)<input className="input" placeholder="BL100012345" value={f.order_number} onChange={set('order_number')} /></label>
            <label className="field">Subject<input className="input" value={f.subject} onChange={set('subject')} /></label>
            <label className="field">Message<textarea className="input" rows={6} required minLength={3} value={f.message} onChange={set('message')} /></label>
            <p className="small muted" style={{ margin: 0 }}>If a light is sparking, smoking or smells of burning, switch it off at the circuit breaker now. If there's fire, call 000.</p>
            {err && <div className="error" role="alert">{err}</div>}
            <div><button className="btn primary" disabled={busy} type="submit">{busy ? 'Sending…' : 'Send message'}</button></div>
          </>)}
        </form>
      </div>
    </div>
  )
}
