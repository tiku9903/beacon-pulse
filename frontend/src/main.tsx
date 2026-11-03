import React, { createContext, useCallback, useContext, useEffect, useState } from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Navigate, NavLink, Outlet, Route, Routes, useNavigate } from 'react-router-dom'
import { Agent, api, auth, Meta, TicketSummary } from './api'
import './styles.css'
import Login from './pages/Login'
import Inbox from './pages/Inbox'
import Approvals from './pages/Approvals'
import Analytics from './pages/Analytics'
import Team from './pages/Team'
import Settings from './pages/Settings'
import HelpCentre from './pages/HelpCentre'
import ChatWidget from './components/ChatWidget'
import { Bolt } from './components/Bits'

type Session = { agent: Agent | null; meta: Meta | null; setAgent: (a: Agent | null) => void; refreshCounts: () => void; counts: Counts }
type Counts = { open: number; mine: number; approvals: number; safety: TicketSummary[] }
const Ctx = createContext<Session>(null as unknown as Session)
export const useSession = () => useContext(Ctx)

function Shell() {
  const { agent, setAgent, counts } = useSession()
  const nav = useNavigate()
  if (!agent) return <Navigate to="/login" replace />
  const safety = counts.safety
  return (
    <div className="shell">
      <nav className="rail" aria-label="Main">
        <NavLink to="/" className="brand">
          <span className="mark"><Bolt /></span>
          <div><b>BEACON PULSE</b><span>Customer service console</span></div>
        </NavLink>
        <NavLink end to="/" className="switch">Queue {counts.open > 0 && <span className="count">{counts.open}</span>}</NavLink>
        <NavLink to="/mine" className="switch">Assigned to me {counts.mine > 0 && <span className="count">{counts.mine}</span>}</NavLink>
        <NavLink to="/safety" className="switch">Safety alerts {safety.length > 0 && <span className="count alarm">{safety.length}</span>}</NavLink>
        <NavLink to="/approvals" className="switch">Approvals {counts.approvals > 0 && <span className="count">{counts.approvals}</span>}</NavLink>
        <div className="sep" />
        <NavLink to="/analytics" className="switch">Performance</NavLink>
        <NavLink to="/team" className="switch">Team</NavLink>
        <NavLink to="/settings" className="switch">Settings</NavLink>
        <NavLink to="/help" className="switch" target="_blank">Customer help page</NavLink>
        <div className="who">
          <b>{agent.name}</b>
          <span className="muted">{agent.role}</span>{' '}
          <button className="btn ghost small" style={{ color: '#D8D6CF', padding: '2px 6px' }} onClick={() => { auth.set(null); setAgent(null); nav('/login') }}>Sign out</button>
        </div>
      </nav>
      <div className="main">
        {safety.length > 0 && (
          <div className="safety-band" role="alert">
            <span className="pulse-dot" />
            {safety.length === 1 ? `Safety report open: ${safety[0].ref} from ${safety[0].contact_name ?? 'a customer'}` : `${safety.length} safety reports are open`}
            <NavLink to="/safety">Review now</NavLink>
          </div>
        )}
        <Outlet />
      </div>
    </div>
  )
}

function App() {
  const [agent, setAgent] = useState<Agent | null>(null)
  const [meta, setMeta] = useState<Meta | null>(null)
  const [ready, setReady] = useState(false)
  const [counts, setCounts] = useState<Counts>({ open: 0, mine: 0, approvals: 0, safety: [] })

  useEffect(() => {
    api<Meta>('/api/meta').then(setMeta).catch(() => {})
    if (!auth.token) { setReady(true); return }
    api<Agent>('/api/auth/me').then(setAgent).catch(() => auth.set(null)).finally(() => setReady(true))
  }, [])

  const refreshCounts = useCallback(() => {
    if (!auth.token) return
    Promise.all([
      api<{ total: number }>('/api/tickets?open_only=true&limit=1'),
      api<{ total: number }>('/api/tickets?open_only=true&mine=true&limit=1'),
      api<unknown[]>('/api/approvals'),
      api<TicketSummary[]>('/api/alerts'),
    ]).then(([o, m, a, s]) => setCounts({ open: o.total, mine: m.total, approvals: a.length, safety: s })).catch(() => {})
  }, [])

  useEffect(() => {
    if (!agent) return
    refreshCounts()
    const id = setInterval(refreshCounts, 15000)
    return () => clearInterval(id)
  }, [agent, refreshCounts])

  if (!ready) return null
  return (
    <Ctx.Provider value={{ agent, meta, setAgent, refreshCounts, counts }}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/help" element={<HelpCentre />} />
        <Route path="/widget" element={<div style={{ height: '100vh' }}><ChatWidget /></div>} />
        <Route element={<Shell />}>
          <Route path="/" element={<Inbox view="queue" />} />
          <Route path="/mine" element={<Inbox view="mine" />} />
          <Route path="/safety" element={<Inbox view="safety" />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/team" element={<Team />} />
          <Route path="/settings" element={<Settings />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Ctx.Provider>
  )
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><BrowserRouter><App /></BrowserRouter></React.StrictMode>,
)
