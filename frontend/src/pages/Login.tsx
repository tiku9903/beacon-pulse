import { FormEvent, useEffect, useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { Agent, api, auth } from '../api'
import { useSession } from '../main'

const DEMO = [
  ['Alex Turner', 'supervisor', 'alex.turner@beaconpulse.demo'],
  ['Emma Collins', 'customer support agent', 'emma.collins@beaconpulse.demo'],
  ['Mia Robinson', 'warranty agent', 'mia.robinson@beaconpulse.demo'],
  ['Pulse Admin', 'admin', 'admin@beaconpulse.demo'],
]

export default function Login() {
  const { agent, setAgent } = useSession()
  const nav = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [demo, setDemo] = useState(false)

  useEffect(() => { api<{ demo: boolean }>('/api/health').then(h => setDemo(h.demo)).catch(() => {}) }, [])
  if (agent) return <Navigate to="/" replace />

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true); setError('')
    try {
      const r = await api<{ token: string; agent: Agent }>('/api/auth/login', { method: 'POST', json: { email, password } })
      auth.set(r.token); setAgent(r.agent); nav('/')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed. Check your email and password.')
    } finally { setBusy(false) }
  }

  return (
    <div className="login">
      <div className="login-art">
        <div className="glow" />
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, position: 'relative' }}>
          <b style={{ fontStretch: '125%', fontWeight: 800 }}>BEACON LIGHTING</b>
        </div>
        <div style={{ position: 'relative' }}>
          <h1>Beacon<br />Pulse</h1>
          <p>Every enquiry, from every channel, classified and routed in seconds. Routine questions answered on the spot; everything else arrives with a draft ready.</p>
        </div>
      </div>
      <div className="login-form">
        <form onSubmit={submit}>
          <h2>Sign in to the console</h2>
          <label className="field">Work email<input className="input" type="email" autoComplete="username" value={email} onChange={e => setEmail(e.target.value)} required /></label>
          <label className="field">Password<input className="input" type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required /></label>
          {error && <div className="error" role="alert">{error}</div>}
          <button className="btn primary" disabled={busy} type="submit">{busy ? 'Signing in…' : 'Sign in'}</button>
          {demo && (
            <div className="demo-accounts">
              Demo accounts (password <code>BeaconDemo!2026</code>):
              {DEMO.map(([n, r, e]) => (
                <div key={e}><button type="button" onClick={() => { setEmail(e); setPassword('BeaconDemo!2026') }}>{n}</button> — {r}</div>
              ))}
            </div>
          )}
        </form>
      </div>
    </div>
  )
}
