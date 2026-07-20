# ClaimBack 💸

**Recover money companies owe you.**

ClaimBack takes your uploaded documents (medical bills, rejection emails, airline notices, subscription charges, warranty denials) and uses Claude AI to:

1. Identify exactly what happened and what money is at stake
2. Find the correct dispute path (chargeback, regulatory complaint, escalation)
3. Build the strongest legal case with relevant consumer protection laws
4. Draft a ready-to-send dispute letter
5. Stop for your approval before anything is submitted

---

## Quick Start

```bash
cd claimback-main

# 1. Install dependencies (Python 3.10+ recommended)
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

# 2. Browser automation uses your installed Google Chrome directly
#    (channel="chrome") — no `playwright install` download needed.
#    Just make sure Google Chrome is installed.

# 3. Set your API key
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY

# 4. Run  (use_reloader is already disabled in app.py — required so the single
#    Playwright-owned browser lives in one process)
export ANTHROPIC_API_KEY=...     # or rely on .env
.venv/bin/python app.py
# Open http://localhost:5000
```

## Demo flow (airline / EU261)

1. Click **"Try the airline demo"** — loads a canned cancelled-flight email + hotel receipt.
2. ClaimBack analyzes and shows EU261 eligibility with a fixed statutory amount (e.g. **€400**).
3. Click **Approve & Auto-fill** — a **real Chrome window opens** and types the claim into the
   SkyClaim reimbursement portal field-by-field, then **stops at the submit button**.
4. ClaimBack shows a "here's exactly what I'll submit" review table.
5. Click **Confirm & Submit** — the browser clicks submit, the portal returns a reference
   (`SKY-XXXXXX`), and the claim is tracked as *submitted* on the dashboard.

Persistence is a plain JSON file (`claims.json`) — open it to inspect or reset the demo.


---

## Architecture

```
claimback/
├── app.py                  # Flask routes + Claude API calls
├── requirements.txt
├── .env.example
├── templates/
│   └── index.html          # Single-page UI
└── static/
    ├── css/style.css       # Full design system
    └── js/app.js           # File upload, API calls, results rendering
```

### Flow

```
User uploads doc(s)
       ↓
POST /analyze
       ↓
Claude (claude-sonnet-4-6) → JSON analysis
       ↓
Results rendered: summary, legal basis, dispute plan, draft letter
       ↓
If missing info → questions shown → user answers → POST /refine → loop
       ↓
User approves → POST /approve → claim package returned
       ↓
User copies letter / opens form URL / sends email
```

### Claude API Contract

Every response from Claude is strict JSON with these fields:

| Field | Description |
|---|---|
| `claim_type` | What kind of claim this is |
| `summary` | 1-2 sentence plain-English summary |
| `amount_at_stake` | Dollar amount or "Unknown" |
| `what_happened` | Detailed explanation |
| `legal_basis` | Array of applicable laws/regulations |
| `strength` | "Strong" / "Moderate" / "Weak" |
| `strength_reason` | Why |
| `dispute_path.primary` | Best dispute method |
| `dispute_path.steps` | Ordered action steps |
| `dispute_path.escalation` | Fallback if primary fails |
| `missing_evidence` | Documents still needed |
| `questions` | Clarifying questions for user |
| `draft_letter` | Full ready-to-send letter text |
| `submit_to` | Name, method, address, form_url |
| `ready_to_submit` | Boolean |

---

## Supported Claim Types

- Medical bill disputes (HIPAA, state billing laws, surprise billing)
- Rejected insurance reimbursements (ACA, state mandates)
- Damaged/lost deliveries (carrier liability, credit card protection)
- Airline disruptions (DOT rules, EC261 for EU flights)
- Unauthorized subscription charges (FTC rules, credit card chargebacks)
- Warranty rejections (Magnuson-Moss Warranty Act)
- Insurance EOBs (appeals process, state insurance commission)

---

## Extending ClaimBack

### Add auto-form filling
In `/approve`, use Playwright or Selenium to navigate to `submit_to.form_url` and fill fields automatically before showing the approval screen.

### Add Gmail/email forwarding
Add a `/webhook/email` route that accepts inbound email via SendGrid/Mailgun, parses attachments, and runs the analysis automatically.

### Add a database
Replace `session[]` with SQLAlchemy models to store claim history, track outcomes, and build a dashboard.

### Add document OCR
For scanned PDFs, add Tesseract or AWS Textract before sending to Claude.
