export type Agent = { id: number; name: string; email: string; role: string; skills: string[]; max_active_tickets: number; active: boolean; open_tickets?: number }

export type TicketSummary = {
  id: number; ref: string; channel: string; subject: string | null; preview: string; status: string
  intent: string | null; intent_confidence: number | null; sentiment: string | null; urgency: number
  priority: string; team: string | null; team_label: string | null; safety_flag: boolean; safety_reasons: string[]
  contact_name: string | null; contact_email: string | null; customer_segment: string | null; trade_tier: string | null
  assigned_agent: { id: number; name: string } | null; sla_due_at: string | null; sla_breached: boolean
  first_response_at: string | null; resolved_at: string | null; resolution_type: string | null; csat_score: number | null
  pending_approvals: number; created_at: string; updated_at: string
}

export type Message = { id: number; direction: 'inbound' | 'outbound' | 'internal'; author_type: string; author_name: string | null; body: string; channel: string | null; delivered: boolean; created_at: string }
export type Draft = { id: number; body: string; status: string; was_edited: boolean; kb_sources: { id: string; doc: string; title: string }[]; created_at: string }
export type Approval = { id: number; ticket_id: number; ticket_ref: string | null; action_type: string; amount: number | null; payload: Record<string, unknown>; ai_recommendation: string | null; status: string; decided_by: string | null; decision_note: string | null; created_at: string; decided_at: string | null; contact_name: string | null; intent: string | null }
export type AuditEvent = { id: number; event: string; actor: string; data: Record<string, unknown>; created_at: string }

export type TicketDetail = TicketSummary & {
  body: string; entities: Record<string, unknown>; classification_reasoning: string | null; ai_provider: string | null
  agent_corrected_intent: string | null; contact_phone: string | null
  customer: { id: number; crm_id: string; name: string; email: string; phone: string; segment: string; trade_tier: string | null; lifetime_value: number } | null
  messages: Message[]; drafts: Draft[]; approvals: Approval[]; events: AuditEvent[]
}

export type Meta = { intents: Record<string, string>; teams: Record<string, string>; sla_hours: Record<string, number>; statuses: string[] }

const TOKEN_KEY = 'bp_token'
export const auth = {
  get token() { try { return localStorage.getItem(TOKEN_KEY) } catch { return null } },
  set(t: string | null) { try { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY) } catch { /* storage unavailable */ } },
}

export class ApiError extends Error { constructor(public status: number, message: string) { super(message) } }

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string> || {}) }
  if (auth.token) headers.Authorization = `Bearer ${auth.token}`
  let body = init.body
  if (init.json !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(init.json) }
  const res = await fetch(path, { ...init, headers, body })
  if (res.status === 401 && auth.token && !path.startsWith('/api/auth')) { auth.set(null); window.location.href = '/login' }
  if (!res.ok) {
    let msg = res.statusText
    try { const j = await res.json(); msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail) } catch { /* no body */ }
    throw new ApiError(res.status, msg)
  }
  return res.json() as Promise<T>
}

export const label = (s: string | null | undefined) => (s ?? '').replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase())

export function ago(iso: string | null) {
  if (!iso) return '—'
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}

export function hoursText(h: number | null | undefined) {
  if (h === null || h === undefined) return '—'
  if (h < 1) return `${Math.round(h * 60)} min`
  return `${h.toFixed(1)} h`
}

export const money = (n: number | null | undefined) => n == null ? '—' : n.toLocaleString('en-AU', { style: 'currency', currency: 'AUD', maximumFractionDigits: n >= 1000 ? 0 : 2 })
