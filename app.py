import os
import json
import logging

from flask import Flask, render_template, request, jsonify, session, url_for

try:
    from dotenv import load_dotenv
    load_dotenv()  # optional: load env from a .env file if present
except ImportError:
    pass

import llm
import store
from ocr import process_document
from portals import portal_for_claim_type, detect_target
from browser_agent import BrowserAgent, BrowserError

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ── Models ───────────────────────────────────────────────────────────────────
# Passed to the local `claude` CLI (uses your Claude subscription; no API key).
ANALYSIS_MODEL = "opus"     # main reasoning / analysis
MAPPING_MODEL  = "sonnet"   # fast structured field-mapping

# ── App setup ──────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24))
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32 MB upload limit

# ── Claude prompt ──────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are ClaimBack, an expert consumer rights advocate AI.
Your job is to help users recover money owed to them by companies.

You will be given documents (bills, emails, rejection notices, etc.) and must:
1. Identify exactly what happened and what money/rights are at stake
2. Determine the claim type and relevant consumer protection laws
3. Identify the correct dispute process (chargeback, regulatory complaint, company escalation, etc.)
4. Gather any missing evidence needed
5. Draft the strongest possible claim letter/form

AIRLINE CLAIMS — EU261 / UK261 eligibility (apply when the flight departed the EU/UK
or was operated by an EU/UK carrier). Compensation is a FIXED statutory amount, separate
from any expense reimbursement:
  • €250  — flights up to 1,500 km, delay at arrival ≥ 3h (or cancellation < 14 days notice)
  • €400  — flights 1,500–3,500 km (and all intra-EU over 1,500 km), delay ≥ 3h
  • €600  — flights over 3,500 km, delay ≥ 4h
Compensation is NOT owed if the airline proves "extraordinary circumstances" (e.g. severe
weather, ATC strikes) that could not have been avoided. Separately, airlines owe "duty of
care" (hotel, meals, transport) during long disruptions regardless of the cause — claim
these as expense reimbursement with receipts.
When a claim is EU261/UK261 eligible, state the EXACT entitlement (e.g. "€400") in
amount_at_stake and cite the specific rule in strength_reason
(e.g. "€400 — 1,500–3,500 km flight, arrival delay >3h, not extraordinary circumstances").

Always respond in valid JSON with this structure:
{
  "claim_type": "string",
  "summary": "1-2 sentence plain-English summary of what happened",
  "amount_at_stake": "dollar amount or 'Unknown'",
  "what_happened": "detailed explanation",
  "legal_basis": ["list of relevant laws/regulations/policies"],
  "strength": "Strong|Moderate|Weak",
  "strength_reason": "why this claim is strong/moderate/weak",
  "dispute_path": {
    "primary": "Best dispute method",
    "steps": ["ordered list of action steps"],
    "escalation": "What to do if primary fails"
  },
  "missing_evidence": ["list of documents/info still needed, empty if none"],
  "questions": ["clarifying questions to ask user, empty if none"],
  "draft_letter": "Full text of the dispute letter/claim ready to send",
  "submit_to": {
    "name": "Organization/company name",
    "method": "email|web_form|mail|phone",
    "address": "contact details",
    "form_url": "URL if web form, else null"
  },
  "ready_to_submit": true
}"""


# ── Claude helpers (via local `claude` CLI) ─────────────────────────────────

def analyze_claim(documents: list, user_context: str = "") -> tuple:
    """Build a single text prompt from the documents (attaching any image/PDF
    files for the CLI to read) and get the analysis JSON."""
    parts = []
    file_paths = []

    for doc in documents:
        if doc.get("text") is not None:
            parts.append(f"Document ({doc.get('name', 'file')}):\n{doc['text']}")
        elif doc.get("data"):
            path = llm.write_temp_file(doc["data"], doc.get("type", ""),
                                       doc.get("name", ""))
            file_paths.append(path)
            parts.append(f"Attached document to read at path: {path} "
                         f"({doc.get('name', 'file')})")

    if user_context:
        parts.append(f"Additional context: {user_context}")

    parts.append("Analyze this claim and respond ONLY with valid JSON. "
                 "No markdown, no preamble.")
    user_text = "\n\n".join(parts)

    analysis = llm.generate_json(SYSTEM_PROMPT, user_text,
                                 model=ANALYSIS_MODEL, file_paths=file_paths)
    return analysis, {"user_text": user_text}


def refine_claim(analysis: dict, user_message: str, history) -> tuple:
    prev = history.get("user_text", "") if isinstance(history, dict) else ""
    prompt = (
        f"Original claim input:\n{prev}\n\n"
        f"Your previous analysis (JSON):\n{json.dumps(analysis)}\n\n"
        f"The user now provides more information / a correction:\n{user_message}\n\n"
        "Respond ONLY with the full, updated valid JSON. No markdown, no preamble."
    )
    updated = llm.generate_json(SYSTEM_PROMPT, prompt, model=ANALYSIS_MODEL)
    return updated, history


def map_fields_with_claude(analysis: dict, portal: dict) -> dict:
    """Ask Claude to map the extracted claim data onto the portal's form fields.
    Returns {field_name: value}. This is the genuine 'AI fills the form' step —
    the portal registry guarantees the selectors are real."""
    descriptions = portal.get("descriptions", {})
    selects = portal.get("selects", {})

    field_lines = []
    for name in portal.get("fields", {}):
        desc = descriptions.get(name, name)
        if name in selects:
            desc += f"  (choose exactly one of: {', '.join(selects[name])})"
        field_lines.append(f"- {name}: {desc}")
    fields_block = "\n".join(field_lines)

    prompt = (
        "You are filling a company's online claim form on behalf of a user.\n"
        "Here is the extracted claim data (JSON):\n\n"
        f"{json.dumps(analysis, indent=2)}\n\n"
        "Map this data onto the form fields below. Output ONLY a flat JSON object "
        "mapping field names to string values. Only include a field if you can "
        "confidently fill it from the data — omit anything you'd be guessing at.\n"
        "Rules: dates as yyyy-mm-dd; amounts as digits only (e.g. 342.00); "
        "for any field with a fixed option list, use exactly one of the listed values.\n\n"
        f"FORM FIELDS:\n{fields_block}\n\n"
        "Respond ONLY with the JSON object. No markdown, no preamble."
    )

    mapped = llm.generate_json(
        "You map extracted claim data onto web form fields and return only valid JSON.",
        prompt, model=MAPPING_MODEL,
    )

    # Keep only known fields; validate select values against their option lists.
    clean = {}
    known = portal.get("fields", {})
    for name, value in (mapped or {}).items():
        if name not in known or value in (None, ""):
            continue
        if name in selects and str(value) not in selects[name]:
            continue
        clean[name] = str(value)
    return clean


# ── Routes ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    session.clear()
    stats = store.dashboard_stats()
    recent = store.all_claims()[:5]
    return render_template("index.html", stats=stats, recent_claims=recent)


@app.route("/dashboard")
def dashboard():
    claims = store.all_claims()
    stats = store.dashboard_stats()
    return render_template("dashboard.html", claims=claims, stats=stats)


@app.route("/claim/<int:claim_id>")
def view_claim(claim_id):
    claim = store.get_claim(claim_id)
    if not claim:
        return render_template("claim_detail.html", claim=None), 404
    return render_template("claim_detail.html", claim=claim)


@app.route("/portal")
def portal():
    """Mock airline reimbursement portal that the browser agent fills."""
    return render_template("portal.html")


@app.route("/analyze", methods=["POST"])
def analyze():
    documents = []
    user_context = request.form.get("context", "")
    ocr_used = False
    ocr_pages = 0
    doc_names = []

    for f in request.files.getlist("documents"):
        if not f.filename:
            continue
        file_bytes = f.read()
        mime = f.content_type or "application/octet-stream"
        doc_names.append(f.filename)

        doc = process_document(file_bytes, mime, f.filename)
        if doc.get("ocr_used"):
            ocr_used = True
            ocr_pages += doc.get("ocr_pages", 0)
        documents.append(doc)

    pasted = request.form.get("pasted_text", "").strip()
    if pasted:
        documents.append({"name": "Pasted content", "type": "text/plain", "text": pasted,
                          "ocr_used": False, "ocr_pages": 0})
        doc_names.append("pasted_text")

    if not documents:
        return jsonify({"error": "Please upload at least one document or paste your text."}), 400

    # Create a record immediately so we have an ID
    claim = store.add_claim(document_names=doc_names, ocr_used=ocr_used,
                            ocr_pages=ocr_pages, status="pending")

    try:
        analysis, history = analyze_claim(documents, user_context)
        store.apply_analysis(claim["id"], analysis)
        store.update_claim(claim["id"], history=history)

        # Keep the cookie small: store ONLY the id, read the rest from the store.
        session["claim_id"] = claim["id"]
        return jsonify({"analysis": analysis, "claim_id": claim["id"],
                        "ocr_used": ocr_used, "ocr_pages": ocr_pages})
    except Exception as e:
        log.exception("Analysis failed")
        store.update_claim(claim["id"], status="error")
        return jsonify({"error": str(e)}), 500


def _active():
    """(claim_id, analysis, history) for the current session, from the store."""
    claim_id = session.get("claim_id")
    if not claim_id:
        return None, None, None
    claim = store.get_claim(claim_id)
    if not claim:
        return claim_id, None, None
    return claim_id, claim.get("raw_analysis"), claim.get("history") or {}


@app.route("/refine", methods=["POST"])
def refine():
    data = request.get_json()
    user_message = data.get("message", "")
    claim_id, analysis, history = _active()

    if not user_message:
        return jsonify({"error": "No message provided"}), 400
    if not claim_id or analysis is None:
        return jsonify({"error": "No active claim"}), 400

    try:
        updated, _ = refine_claim(analysis, user_message, history)
        store.apply_analysis(claim_id, updated)   # preserves stored history
        return jsonify({"analysis": updated})
    except Exception as e:
        log.exception("Refine failed")
        return jsonify({"error": str(e)}), 500


@app.route("/approve", methods=["POST"])
def approve():
    claim_id, analysis, _ = _active()
    if not analysis:
        return jsonify({"error": "No active claim"}), 400

    store.update_claim(claim_id, status="approved")

    return jsonify({
        "approved": True,
        "claim_id": claim_id,
        "claim_package": {
            "letter": analysis.get("draft_letter"),
            "submit_to": analysis.get("submit_to"),
            "amount": analysis.get("amount_at_stake"),
            "legal_basis": analysis.get("legal_basis"),
        }
    })


@app.route("/autofill", methods=["POST"])
def autofill():
    """Fill a claim form and STOP at submit (pause for approval). Two modes:
      • company text (Delta/FedEx) → drive the REAL site, scrape fields live,
        Claude maps → fill (fill-only, never auto-submit).
      • otherwise → the deterministic mock SkyClaim portal (guaranteed demo)."""
    claim_id, analysis, _ = _active()
    if not analysis or not claim_id:
        return jsonify({"error": "No active claim to auto-fill."}), 400

    body = request.get_json(silent=True) or {}
    company = (body.get("company") or "").strip()
    target = detect_target(company) if company else None

    # ── Real-site dynamic path (fill-only) ──
    if target:
        agent = BrowserAgent.instance()
        try:
            fields = agent.open_and_scrape(
                claim_id, target["url"], target.get("dismiss_selectors"))
        except BrowserError as e:
            return jsonify({"error": str(e)}), 502
        except Exception as e:
            log.exception("Scrape failed")
            return jsonify({"error": f"Could not open {target['company']}'s site: {e}"}), 502

        if not fields:
            return jsonify({"error": f"No form fields found on {target['company']}'s page "
                                     "(it may need login, be multi-step, or use a captcha)."}), 502

        # Walk the multi-step wizard: fill this section → Continue → re-scrape →
        # repeat. Stop at the last section (never click a final "Submit").
        filled = []
        seen_sigs = set()
        try:
            for step in range(6):  # safety cap
                sig = tuple(sorted(f.get("selector", "") for f in fields))
                if sig in seen_sigs:
                    break  # no progress (validation blocked) — stop
                seen_sigs.add(sig)

                mappings = llm.map_dynamic(fields, analysis)
                filled += agent.fill_scraped(claim_id, mappings)

                adv = agent.advance(claim_id)          # click Continue if present
                if not adv.get("advanced"):
                    break                              # last section reached — stop
                fields = agent.scrape_open(claim_id)   # next section
        except BrowserError as e:
            return jsonify({"error": str(e)}), 502
        except Exception as e:
            log.exception("Dynamic wizard fill failed")
            return jsonify({"error": f"Browser could not fill the form: {e}"}), 502

        store.update_claim(claim_id, status="approved", autofill_mode="real")
        return jsonify({
            "filled": filled,
            "portal_label": f"{target['company']} (live site)",
            "portal_url": target["url"],
            "auto_submit": False,
        })

    # ── Mock deterministic path (guaranteed) ──
    portal = portal_for_claim_type(analysis.get("claim_type"))
    try:
        values = map_fields_with_claude(analysis, portal)
    except Exception as e:
        log.exception("Field mapping failed")
        return jsonify({"error": f"Could not map claim to the form: {e}"}), 500

    if not values:
        return jsonify({"error": "No form fields could be filled from this claim."}), 400

    portal_url = portal.get("url") or url_for(portal.get("url_endpoint", "portal"),
                                              _external=True)
    try:
        filled = BrowserAgent.instance().open_and_fill(
            claim_id, portal_url, portal, values)
    except BrowserError as e:
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        log.exception("Autofill failed")
        return jsonify({"error": f"Browser could not fill the form: {e}"}), 502

    store.update_claim(claim_id, status="approved", autofill_mode="mock")
    return jsonify({
        "filled": filled,
        "portal_label": portal.get("label"),
        "portal_url": portal_url,
        "auto_submit": portal.get("auto_submit", True),
    })


@app.route("/autofill/submit", methods=["POST"])
def autofill_submit():
    """Approved — for the mock portal, click submit and capture the reference.
    Real sites (autofill_mode='real') are FILL-ONLY: never file a real claim."""
    claim_id, analysis, _ = _active()
    if not claim_id:
        return jsonify({"error": "No active claim."}), 400

    claim = store.get_claim(claim_id) or {}
    if claim.get("autofill_mode") == "real":
        store.update_claim(claim_id, status="filled")
        return jsonify({
            "submitted": False, "manual": True,
            "message": "This is the company's real site — the form is filled and waiting "
                       "in the browser. Review and submit it manually.",
        })

    try:
        reference = BrowserAgent.instance().submit(claim_id)  # mock portal defaults
    except BrowserError as e:
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        log.exception("Submit failed")
        return jsonify({"error": f"Submission failed: {e}"}), 502

    store.update_claim(claim_id, status="submitted", submission_reference=reference)
    return jsonify({"submitted": True, "reference": reference})


ESCALATE_SYSTEM = """You are ClaimBack's escalation drafter. The company refused or ignored a
valid consumer claim, so the user is escalating to the appropriate authority.

Pick the correct authority for the claim type:
  • Airline (US): U.S. DOT Aviation Consumer Protection — bring up their complaint channel.
  • Airline (EU/UK): the national enforcement body / CAA.
  • Lost/stolen package or suspected theft: the local police non-emergency line (file a report).
  • Shipping damage: the carrier's formal claims escalation.
  • Otherwise: the FTC and/or the state Attorney General (California).

Write a firm, professional escalation email FROM the claimant. Refer to the claimant by
the name in the claim data (or "the claimant") within the sentences as needed.

The "body" is an EMAIL body — NOT a mailed letter. So:
  • Start directly with the salutation, e.g. "Dear Office of Aviation Consumer Protection,"
    (or "Dear <authority>,").
  • Do NOT include any letterhead: no sender name/address block, no date, no recipient
    address block, and no "Re:" subject line inside the body. The subject is separate.
  • End with a simple sign-off and the claimant's name (e.g. "Sincerely, Jane Doe"),
    optionally followed by their email/phone if present in the claim.

Respond ONLY with valid JSON:
{"authority": "name of the body", "to": "a plausible contact email for that body",
 "subject": "email subject", "body": "email body starting at the salutation"}"""


@app.route("/escalate", methods=["POST"])
def escalate():
    """Draft an escalation email to the right authority for a refused claim."""
    claim_id, analysis, _ = _active()
    if not analysis or not claim_id:
        return jsonify({"error": "No active claim to escalate."}), 400

    prompt = ("The company refused or ignored this claim. Draft the escalation email.\n\n"
              f"CLAIM DATA (JSON):\n{json.dumps(analysis, indent=2)}")
    try:
        draft = llm.generate_json(ESCALATE_SYSTEM, prompt, model=ANALYSIS_MODEL)
    except Exception as e:
        log.exception("Escalation draft failed")
        return jsonify({"error": f"Could not draft the escalation: {e}"}), 500

    store.update_claim(claim_id, status="escalated")
    return jsonify({
        "authority": draft.get("authority"),
        "to": draft.get("to", ""),
        "subject": draft.get("subject", "Consumer complaint — escalation"),
        "body": draft.get("body", ""),
    })


@app.route("/api/portals")
def api_portals():
    """Portals available for the (optional) picker."""
    return jsonify({"portals": [{"key": "airline", "label": "SkyClaim (safe demo portal)"}]})


@app.route("/claim/<int:claim_id>/resolve", methods=["POST"])
def resolve_claim(claim_id):
    """Mark a claim as resolved and record how much was recovered."""
    claim = store.get_claim(claim_id)
    if not claim:
        return jsonify({"error": "Claim not found"}), 404
    data = request.get_json()

    recovered_cents = store.parse_cents(data.get("recovered", "0")) or 0
    updated = store.resolve_claim(claim_id, recovered_cents, data.get("note", ""))
    return jsonify({"ok": True, "claim": updated})


@app.route("/api/claims")
def api_claims():
    """JSON endpoint for dashboard data."""
    return jsonify({"claims": store.all_claims(), "stats": store.dashboard_stats()})


if __name__ == "__main__":
    # use_reloader=False: the reloader spawns two processes that would fight over
    # the single Playwright-owned browser. Critical for the live demo.
    app.run(debug=True, port=5000, use_reloader=False)
