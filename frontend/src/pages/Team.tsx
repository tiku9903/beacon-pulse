import { FormEvent, useEffect, useState } from 'react'
import { Agent, api } from '../api'
import { useSession } from '../main'

type TeamRow = { team: string; label: string; open: number; P1: number; P2: number; P3: number; P4: number; breached: number; unassigned: number }

export default function Team() {
  const { agent, meta } = useSession()
  const [agents, setAgents] = useState<Agent[]>([])
  const [teams, setTeams] = useState<TeamRow[]>([])
  const [form, setForm] = useState({ name: '', email: '', password: '', role: 'agent', skills: [] as string[] })
  const [msg, setMsg] = useState('')
  const load = () => {
    api<Agent[]>('/api/agents').then(setAgents).catch(() => {})
    api<{ teams: TeamRow[] }>('/api/queues/summary').then(r => setTeams(r.teams)).catch(() => {})
  }
  useEffect(load, [])

  async function add(e: FormEvent) {
    e.preventDefault(); setMsg('')
    try {
      await api('/api/agents', { method: 'POST', json: form })
      setMsg(`Added ${form.name}. They can sign in now.`)
      setForm({ name: '', email: '', password: '', role: 'agent', skills: [] }); load()
    } catch (err) { setMsg(err instanceof Error ? err.message : 'Could not add the agent.') }
  }

  return (
    <div className="page">
      <div className="page-head"><div><h1>Team</h1><p className="muted" style={{ margin: '4px 0 0' }}>Live queue load per team. New tickets go to the least-loaded agent who has the team's skill.</p></div></div>

      <table className="grid">
        <thead><tr><th>Team</th><th className="r">Open</th><th className="r">P1</th><th className="r">P2</th><th className="r">P3</th><th className="r">P4</th><th className="r">Unassigned</th><th className="r">Overdue first reply</th></tr></thead>
        <tbody>{teams.map(t => (
          <tr key={t.team}><td>{t.label}</td><td className="r num">{t.open}</td><td className="r num">{t.P1}</td><td className="r num">{t.P2}</td><td className="r num">{t.P3}</td><td className="r num">{t.P4}</td><td className="r num">{t.unassigned}</td>
            <td className="r num" style={{ color: t.breached ? 'var(--signal)' : undefined, fontWeight: t.breached ? 700 : 400 }}>{t.breached}</td></tr>
        ))}</tbody>
      </table>

      <h2 style={{ margin: '28px 0 10px' }}>People</h2>
      <table className="grid">
        <thead><tr><th>Name</th><th>Role</th><th>Skills</th><th className="r">Open tickets</th><th className="r">Capacity</th></tr></thead>
        <tbody>{agents.map(a => (
          <tr key={a.id}><td>{a.name}<div className="small muted">{a.email}</div></td><td>{a.role}</td>
            <td className="small">{a.skills.map(s => meta?.teams[s] ?? s).join(', ')}</td>
            <td className="r num">{a.open_tickets ?? 0}</td><td className="r num">{a.max_active_tickets}</td></tr>
        ))}</tbody>
      </table>

      {agent?.role === 'admin' && (
        <form className="panel" style={{ marginTop: 24, display: 'grid', gap: 12, maxWidth: 560 }} onSubmit={add}>
          <h2>Add a team member</h2>
          <label className="field">Name<input className="input" required value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></label>
          <label className="field">Work email<input className="input" type="email" required value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} /></label>
          <label className="field">Temporary password<input className="input" type="password" minLength={8} required value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} /></label>
          <label className="field">Role<select className="input" value={form.role} onChange={e => setForm({ ...form, role: e.target.value })}><option value="agent">Agent</option><option value="supervisor">Supervisor (can approve refunds)</option><option value="admin">Admin</option></select></label>
          <fieldset style={{ border: 0, padding: 0, margin: 0 }}><legend className="field" style={{ fontWeight: 600, marginBottom: 6 }}>Teams they work in</legend>
            <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
              {Object.entries(meta?.teams ?? {}).map(([k, v]) => (
                <label key={k} className="small" style={{ display: 'flex', gap: 5, alignItems: 'center' }}>
                  <input type="checkbox" checked={form.skills.includes(k)} onChange={e => setForm({ ...form, skills: e.target.checked ? [...form.skills, k] : form.skills.filter(x => x !== k) })} />{v}
                </label>))}
            </div>
          </fieldset>
          <div><button className="btn primary" type="submit">Add team member</button></div>
          {msg && <div className="small">{msg}</div>}
        </form>
      )}
    </div>
  )
}
