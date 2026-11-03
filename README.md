# Beacon Pulse

AI-powered customer service automation for Beacon Lighting. Runs on free AI (Groq or Gemini), on paid Claude or OpenAI, or with no AI key at all on the built-in rules engine. Every enquiry from email, web form, live chat and SMS lands in one queue. Each one goes through the same five phases:

1. **Intake**: the enquiry is received.
2. **Identify**: the customer is matched in Customer 360 / CRM.
3. **Classify**: intent, entities, sentiment, urgency and a safety scan are worked out.
4. **Route**: the enquiry gets a priority (Safety > Trade/VIP > sentiment), is assigned by skill to the least-loaded agent, and gets an SLA clock.
5. **Resolve**: AI specialist agents answer the customer using live order, stock and policy tools, and submit refunds, returns and warranty claims for a human to approve. Safety issues go to people at P1 with fixed safety advice.

## The AI agent engine

When an AI key is set (Groq by default, free), every customer message is handled by a multi-agent system. If no key is set, or the AI is unavailable, the rules engine answers instead, so tickets are never lost.

```
customer ─▶ Triage (intent, sentiment, safety) ─▶ Specialist agent ⇄ tools ─▶ Guardrail ─▶ reply / human
```

**Specialists.** Each specialist has its own instructions and only the tools its job needs (`backend/app/agents/specialists.py`):

| Specialist | Handles | Tools |
|---|---|---|
| Order agent | Where is my order, delivery, Click & Collect | `find_orders`, `get_customer_profile`, `search_policy` |
| Product agent | Stock, price, restock | `check_product_stock`, `search_policy` |
| Support agent | Policies and troubleshooting | `search_policy`, `check_product_stock`, `find_orders` |
| Returns & refunds agent | Returns, refunds, double charges | `check_return_eligibility`, `create_return_request`, `create_refund_request` |
| Warranty agent | Faulty products | `check_warranty`, `create_warranty_claim` |
| Trade desk agent | Trade customers, bulk orders | `get_customer_profile`, `check_product_stock`, `find_orders` |
| Concierge agent | Anything unclear | read-only tools, plus transfer to a specialist |

Every agent can also call `escalate_safety`, `handoff_to_human` and `transfer_to_specialist`. It ends each turn with `respond_to_customer` and an outcome: resolved, needs_info, pending_review or handed_off.

**Business rules live in the tools, not the prompt** (`backend/app/agents/tools.py`):
- Orders are only shown to the email they belong to. In chat, the customer must type the order number, and tracking numbers are emailed rather than shown.
- The 30-day return window and warranty terms are calculated in code.
- Refunds can't exceed the order total and are only ever *submitted* for supervisor approval.

**Guardrail** (`backend/app/agents/runtime.py`). Before a customer sees a reply, a code check rejects:
- any order number, dollar amount or tracking code that no tool returned;
- any claim that a refund, return or replacement was approved;
- any wiring instructions.

A rejected reply goes back to the agent with the reason, so it can fix it.

**Safety.** If the agent spots a hazard the keyword scan missed, it escalates. The customer gets the fixed, approved safety wording, never AI-written text, and the case goes to the Safety team at P1.

**Memory.** Agents see the whole conversation, including earlier questions in the same chat. Once a human agent has replied on a ticket, the AI only drafts and never sends over them.

**What the team sees.** Each ticket shows:
- an **AI agent** panel with the specialist and every tool call;
- an internal summary note for the human;
- every guardrail block in the timeline.

**Try it** (with `GROQ_API_KEY` set): on `/help`, chat as `priya.raman@example.com` and say *"my ceiling fan stopped working"*. When the agent asks for the order number, reply with `BL100045826`. The Warranty agent checks the warranty, submits a replacement claim, and you can see the whole run in the console.

```
 Email ─┐                      ┌─ Auto-resolve ─── grounded reply (email / SMS / chat)
 Web ───┼─ Intake → Identify → Classify → Route ─┼─ Agent queue ──── AI draft + KB sources
 Chat ──┤      (CRM)   (LLM + rules)  (priority, ├─ Approvals ────── refund / return / replacement
 SMS ───┘                              skills,   └─ Safety alert ─── fixed safety advice + P1 page
                                       SLA)
        Magento orders · Live stock · Policy knowledge base
```

## Run it

**Docker (recommended)**

```bash
cp .env.example .env          # add a free GROQ_API_KEY, or leave blank for the rules engine
docker compose up --build     # Postgres + app
```

Then open:

- Agent console: http://localhost:8080
- Customer help page (contact form + chat): http://localhost:8080/help
- API docs (OpenAPI): http://localhost:8080/docs

**Without Docker**

```bash
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000           # SQLite by default
cd ../frontend && npm install && npm run dev        # http://localhost:5173 (proxies /api)
cd ../backend && pytest                             # 36 end-to-end and regression tests
```

**Demo accounts** (password `BeaconDemo!2026`): `alex.turner@beaconpulse.demo` (supervisor), `emma.collins@beaconpulse.demo` (agent), `admin@beaconpulse.demo` (admin). The demo seeds the team (7 specialists, a supervisor and an admin), 30 days of ticket history and a live queue. To remove the demo tickets, go to Settings > Delete demo tickets, or set `SEED_DEMO_DATA=false`.

### Deploy on Render (free)

1. Get a free AI key at https://console.groq.com/keys (no credit card). This is optional: without a key, the built-in rules engine runs everything.
2. On render.com, go to **New > Blueprint**, connect this GitHub repo, and click **Apply**. Render reads `render.yaml`.
3. Paste `GROQ_API_KEY` when asked.
4. Wait about 5–8 minutes for the first build, then open `https://beacon-pulse-xxxx.onrender.com`. Sign in with `alex.turner@beaconpulse.demo` / `BeaconDemo!2026`.

**Free-tier behaviour**
- The service sleeps after 15 minutes without traffic. The next visit takes about a minute to wake it.
- On every restart the demo data is reset, because the free tier has no persistent disk.
- To keep data, create a free Postgres database at neon.tech and set `DATABASE_URL` in Render > Environment.
- To use Google Gemini instead of Groq, set `AI_PROVIDER=gemini` and `GEMINI_API_KEY`.

For production, upgrade the plan, use Postgres, add the connector variables, and set `SEED_DEMO_DATA=false`. The same Docker image also runs on Railway, Fly.io, Cloud Run, or with `docker compose`. `netlify.toml` is included if you want the frontend on Netlify.

## Five-minute walkthrough

1. On `/help`, send the contact form as `sarah.mitchell@example.com` asking *"where is my order BL100045821?"*. A reply with live tracking goes straight to her inbox (see Settings > Test outbox). Ask the same question in chat and the tracking is emailed rather than shown, because chat identity is self-declared.
2. Try the same question from a different email address. No order details are disclosed; the ticket goes to an agent.
3. Ask *"do you have the Aero DC ceiling fan in stock?"*. You get live stock per store.
4. Send *"my fan is sparking and smells of burning"*. The customer gets immediate safety advice, the ticket becomes P1 for the Safety team, and a red alert band appears in the console.
5. In the console, the queue is sorted by priority and SLA. Open a ticket to see:
   - the AI triage, which you can correct; corrections become training data
   - the grounded draft
   - the customer record
   - the full audit timeline
6. Go to Approvals. A refund needs a supervisor; returns and replacements can be approved by agents. Approving notifies the customer.
7. Go to Performance. It shows the deck's success criteria (60% automated, 30% assisted, 1.2 h response, 95% SLA, 90% accuracy, CSAT 4.8) measured live, plus the $ savings model.

## How the AI is kept safe

| Risk | Control |
|---|---|
| Hallucinated facts in replies | Anything **sent automatically** uses grounded templates, and every fact comes from Magento, stock data or the policy knowledge base. LLM-written text is only ever an **agent draft**, and the prompt forbids facts outside the supplied context. |
| Missed safety issues | A deterministic keyword scan runs on every message. The LLM can *add* a safety flag but never clear one. The flag is sticky across follow-ups, and only a supervisor can clear it (with a logged reason). Safety tickets are never auto-resolved. |
| Wrong IDs | Order numbers and SKUs are extracted by regex and always override the LLM. |
| Data leakage | Order details only ever go to the email address on the order, never into the chat window. Threads can only be continued by their original sender. Chat sessions are bound to a signed token. |
| Money moving | Refunds, returns and replacements always create an approval, and only against a verified order. Refunds need a supervisor (`REFUND_AUTO_APPROVE_LIMIT=0`). Decisions are claimed atomically, so nothing executes twice. |
| Low confidence or unhappy customer | No auto-reply below 80% confidence, for angry sentiment, or after the customer has already replied once to an automated answer. |
| LLM outage | Every AI call falls back to the rules engine; tickets are never lost. Pipeline errors drop the ticket into the human queue. |

## Rollout (maps to the 6-month roadmap)

| Month | Setting | What happens |
|---|---|---|
| 1 Foundations | `ORDER_CONNECTOR=magento`, `EMAIL_TRANSPORT=console` on UAT | Connect Magento, map data, load the real policy text into `backend/knowledge/`. |
| 2 Pilot | `AUTOMATION_MODE=shadow`, then `live` with `AUTO_RESOLVE_INTENTS=order_status` | Parallel run: the AI drafts everything and sends nothing. Track *Agents sent X% of drafts unedited* and intent accuracy on Performance, then go live on Order Status only. |
| 3 Expansion | add `product_availability,store_policy`; returns/warranty approvals | Trade priority lane is on by default (CRM `segment=trade`). |
| 4 Enablement | | Agents work from drafts; Team page for load and capacity. |
| 5 Full deploy | all channels, all 8 paths routed | Safety nets: shadow switch, confidence threshold, approvals. |
| 6 Optimisation | tune threshold and paths from the *By resolution path* table and `/api/analytics/corrections` | |

## Connecting Beacon's systems

All settings are environment variables; see `.env.example` and `backend/app/config.py`.

- **Magento 2 / Adobe Commerce.** Create an Integration with read access to Sales (Orders, Shipments) and Catalog/Inventory. Then set:
  - `ORDER_CONNECTOR=magento`, `INVENTORY_CONNECTOR=magento`, `MAGENTO_BASE_URL`, `MAGENTO_ACCESS_TOKEN`
  - Store-level stock uses one MSI source per store.
  - Approved refunds call `POST /V1/order/{id}/refund` (`backend/app/actions.py`).
- **CRM / Customer 360.** Set `CRM_CONNECTOR=http` and `CRM_BASE_URL`. The CRM should return `segment` (`retail|trade`) and `trade_tier`. For Salesforce, Dynamics or HubSpot, adapt `HTTPCRMConnector._map`.
- **Inbound email.** Either:
  - point your provider's inbound-parse webhook (SendGrid, Mailgun, Postmark, SES) at `POST /api/channels/email/inbound` with header `X-Webhook-Secret`, or
  - set `IMAP_*` to poll the support mailbox.
  
  Replies thread on `[BP-000123]` in the subject or `In-Reply-To`.
- **Outbound email / SMS.** Set `EMAIL_TRANSPORT=smtp` with `SMTP_*`, and `SMS_TRANSPORT=twilio` with `TWILIO_*`. Inbound SMS goes to `POST /api/channels/sms/inbound?secret=…`. Until these are connected, sent messages appear under Settings > Test outbox.
- **Chat on beaconlighting.com.au.** Add `<script src="https://<pulse-host>/widget.js" defer></script>` to the theme footer.
- **Web form.** Use the `/help` page as-is, or post Magento's contact form to `POST /api/channels/web-form`.
- **Safety paging.** Wire `alert_safety_team()` in `backend/app/notify.py` to PagerDuty, Teams or Slack.

## Project layout

```
backend/app/
  pipeline.py        the 5-phase workflow (intake, identify, classify, route, resolve)
  ai/                engine (LLM + merge), providers (Claude / OpenAI), rules (regex, safety, fallback), taxonomy (8 paths, teams)
  connectors/        mock Beacon systems + Magento 2 / HTTP CRM adapters
  routing.py         priority rules, skill-based assignment, SLA
  responses.py       grounded reply templates (the only text sent without a human)
  knowledge.py       policy knowledge base + BM25 retrieval   (content: backend/knowledge/*.md)
  actions.py         executes approved refunds / returns / replacements
  api/               channels, console, analytics, admin
frontend/src/        agent console, approvals, performance, team, settings, help page, chat widget
```

## Before go-live

- [ ] Replace the placeholder policy text in `backend/knowledge/` with Beacon-approved wording (returns, warranty terms, safety advice). The safety acknowledgement in `responses.safety_ack` also needs legal sign-off.
- [ ] Set `ENVIRONMENT=production`, a strong `JWT_SECRET` and `INBOUND_WEBHOOK_SECRET`, and `SEED_DEMO_DATA=false`.
- [ ] Get Magento and CRM credentials, run a parallel run in shadow mode, and confirm accuracy.
- [ ] Add SSO (Azure AD or Okta) in front of the console if required; the built-in login uses PBKDF2 passwords and JWT.
- [ ] The schema is created on start-up. Add Alembic migrations before the first schema change in production.
- [ ] Check the cost model assumptions on the Performance page (`ASSISTED_EFFORT_RATIO`, `PLATFORM_ANNUAL_COST`). At the deck's 60% / 30% mix they reproduce **$670K cost and $1.53M savings** (69.5% reduction).
