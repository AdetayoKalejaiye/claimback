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
# 1. Clone and enter the project
cd claimback

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set your API key
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY

# 4. Run
python app.py
# Open http://localhost:5000
```

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
