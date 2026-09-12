# Churn-Rescue

Autonomous, omnichannel customer-retention engine for subscription businesses.

## Enterprise SaaS Value

Churn-Rescue turns a cancellation click into a live retention conversation. When a customer hits "Cancel Subscription" the system immediately dials them through CALL-E, negotiates a personalized discount based on their lifetime value, and confirms the deal over WhatsApp. A live WebSocket dashboard lets retention teams watch sentiment and outcomes in real time.

| Outcome | How it helps |
| --- | --- |
| Revenue protection | Intercepts cancellations before they finalize |
| Margin protection | Discounts are capped by LTV, not gut feel |
| Faster recovery | Voice + WhatsApp work while the customer is still engaged |
| Team visibility | Live sentiment radar and transcript feed |
| Low overhead | SQLite mock CRM, minimal dependencies, runs anywhere |

## The Retention Flow

1. A downstream system (billing, CRM, self-serve portal) calls `POST /webhook/cancel-subscription`.
2. Churn-Rescue looks up the customer in the SQLite mock CRM and computes their maximum discount from LTV.
3. It queues a CALL-E outbound voice call with a detailed "Executive Retention Specialist" prompt.
4. While the call runs, a background poller fetches transcript turns and scores each customer utterance with VADER.
5. Live sentiment and transcript updates are pushed to `WS /ws` and rendered in the dashboard.
6. If the customer accepts the discount, Churn-Rescue fires a Twilio WhatsApp confirmation and updates the CRM to `saved`.

## Dynamic LTV Discount Calculation

The discount engine starts at a base offer and grows as lifetime value grows. It is clamped by a ceiling so high-LTV customers get a larger incentive, but the margin is always protected.

```text
discount = min(
    discount_max_percent,
    discount_min_percent + floor(ltv / ltv_tier_step) * discount_step_percent
)
```

### Default configuration

```text
DISCOUNT_MIN_PERCENT=20
DISCOUNT_MAX_PERCENT=50
DISCOUNT_STEP_PERCENT=5
LTV_TIER_STEP=500
```

### Example discounts

| Lifetime Value (LTV) | Calculation | Maximum Discount |
| --- | --- | --- |
| $0 - $499 | floor(174 / 500) = 0 → 20% | 20% |
| $500 - $999 | floor(650 / 500) = 1 → 20 + 5 = 25% | 25% |
| $1,000 - $1,499 | floor(1,422 / 500) = 2 → 20 + 10 = 30% | 30% |
| $8,000+ | floor(8,964 / 500) = 17 → 20 + 85 = 105, clamped | 50% |

The actual offer may be lower than the maximum, depending on live sentiment. A frustrated customer is anchored toward the ceiling; a neutral or happy customer is anchored toward the middle.

## Omnichannel: CALL-E Voice + Twilio WhatsApp

Churn-Rescue is not a single-channel chatbot. It coordinates two independent communication channels:

- **CALL-E voice channel** handles the live negotiation. The voice agent is driven by a prompt that includes the customer's name, tier, monthly amount, LTV, and maximum allowed discount. It returns a structured result with cancellation reason, sentiment, discount offered, and acceptance status.
- **Twilio WhatsApp channel** handles the written confirmation. As soon as the structured result shows `accepted: yes`, the backend builds a WhatsApp message, pushes it to the dashboard, and then calls Twilio to deliver it to the customer.

This gives the customer a voice conversation when emotions run high, and a written record they can keep when the deal is closed.

## Setup

### 1. Install dependencies

```powershell
pip install -r requirements.txt
```

### 2. Configure the `.env` file

Copy the example file and fill in your real credentials:

```powershell
cp .env.example .env
```

Edit `.env`:

```text
# CALL-E Developer API credentials
CALLE_API_KEY=your_call_e_api_key_here
CALLE_BASE_URL=https://api.heycall-e.com

# Public URL where CALL-E can POST terminal call events.
# You will get this from ngrok in the next step.
CALLE_WEBHOOK_URL=https://your-ngrok-url/calle/webhook

# Twilio credentials for WhatsApp confirmations
TWILIO_ACCOUNT_SID=your_twilio_account_sid
TWILIO_AUTH_TOKEN=your_twilio_auth_token
# Must be a WhatsApp-enabled Twilio number or Sandbox number in the format:
#   whatsapp:+<E.164>
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886

# App / discount knobs
APP_ENV=development
LOG_LEVEL=INFO
DATABASE_PATH=data/churn_rescue.db
DISCOUNT_MIN_PERCENT=20
DISCOUNT_MAX_PERCENT=50
DISCOUNT_STEP_PERCENT=5
LTV_TIER_STEP=500
```

### 3. Expose your local server with ngrok

CALL-E cannot reach `localhost`, so you need a public tunnel for terminal call webhooks.

1. Start the app on port 8000.

```powershell
python -m uvicorn churn_rescue.main:app --host 0.0.0.0 --port 8000
```

2. In a separate terminal, start ngrok:

```powershell
ngrok http 8000
```

3. ngrok prints a forwarding URL like:

```text
Forwarding  https://abc1-2-3-4.ngrok-free.app -> http://localhost:8000
```

4. Copy that HTTPS URL into `.env`:

```text
CALLE_WEBHOOK_URL=https://abc1-2-3-4.ngrok-free.app/calle/webhook
```

5. Restart the app so it loads the updated `CALLE_WEBHOOK_URL`.

### 4. Twilio Sandbox for WhatsApp (optional, but recommended for demos)

If you are using the Twilio Sandbox, the receiving phone number must opt in by sending your sandbox join code to the sandbox number before you can send messages to it. Set `TWILIO_WHATSAPP_FROM` to the sandbox number shown in your Twilio console, for example `whatsapp:+14155238886`.

### 5. Seed the mock CRM

```powershell
python seed_customers.py
```

This creates sample customers with different tiers and LTVs so you can test the discount math immediately.

### 6. Trigger a retention call

```bash
curl -X POST http://localhost:8000/webhook/cancel-subscription \
  -H "Content-Type: application/json" \
  -d '{"user_id":"cust_001"}'
```

The server will return `202 Accepted` with a `call_id` once CALL-E accepts the task. The background poller then waits for the call to complete. If you configured `CALLE_WEBHOOK_URL`, CALL-E will also send a terminal event to `/calle/webhook`.

### 7. Open the Command Center

```text
http://localhost:8000/dashboard
```

The dashboard shows a live sentiment dial, a glowing radar that speeds up on negative sentiment, a rolling transcript, and a mock WhatsApp phone that displays the generated confirmation message the moment a discount is accepted.

## API Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/webhook/cancel-subscription` | Trigger a CALL-E retention call |
| `POST` | `/calle/webhook` | Receive CALL-E terminal call events |
| `GET` | `/dashboard` | Live command-center UI |
| `WS` | `/ws` | Live sentiment and transcript feed |
| `GET` | `/health` | Service health and integration status |
| `GET` | `/customers` | List mock CRM customers |

## Project Structure

```text
churn_rescue/
  main.py              # FastAPI app, webhooks, WebSocket, dashboard
  calle_client.py      # CALL-E Developer API HTTP client
  twilio_client.py     # Twilio WhatsApp confirmations
  conversation.py      # Retention prompt builder + discount math
  sentiment.py         # VADER-based real-time sentiment
  websocket.py         # Dashboard connection manager
  db.py / crm.py       # SQLite mock CRM
  static/index.html    # Live command-center dashboard
seed_customers.py      # Seed demo customers
.env.example           # Environment variable template
requirements.txt       # Python dependencies
```

## Notes

- If VADER is not installed, `sentiment.py` falls back to a small rule-based lexicon so the server still runs.
- If Twilio credentials are missing, the dashboard still renders the message that *would* be sent, but no real WhatsApp is delivered.
- The `CALLE_WEBHOOK_URL` is optional. Without it, the background poller still retrieves the final result, but it is less resilient to intermittent network issues.
