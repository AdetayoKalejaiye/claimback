import os
import json
import base64
import logging
from datetime import datetime, timezone

from flask import Flask, render_template, request, jsonify, session
import anthropic

from models import db, Claim
from ocr import process_document

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ── App setup ──────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24))

basedir = os.path.abspath(os.path.dirname(__file__))
app.config["SQLALCHEMY_DATABASE_URI"] = (
    os.environ.get("DATABASE_URL")
    or f"sqlite:///{os.path.join(basedir, 'claimback.db')}"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32 MB upload limit

db.init_app(app)

with app.app_context():
    db.create_all()

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# ── Claude prompt ──────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are ClaimBack, an expert consumer rights advocate AI.
Your job is to help users recover money owed to them by companies.

You will be given documents (bills, emails, rejection notices, etc.) and must:
1. Identify exactly what happened and what money/rights are at stake
2. Determine the claim type and relevant consumer protection laws
3. Identify the correct dispute process (chargeback, regulatory complaint, company escalation, etc.)
4. Gather any missing evidence needed
5. Draft the strongest possible claim letter/form

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


# ── Claude helpers ─────────────────────────────────────────────────────────

def _call_claude(messages: list) -> dict:
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=messages,
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(raw)


def analyze_claim(documents: list, user_context: str = "",
                  conversation_history: list = None) -> tuple[dict, list]:
    messages = list(conversation_history or [])
    content = []

    for doc in documents:
        if doc.get("text") is not None:
            content.append({
                "type": "text",
                "text": f"Document ({doc.get('name', 'file')}):\n{doc['text']}"
            })
        elif doc.get("type") in ("image/jpeg", "image/png", "image/gif", "image/webp"):
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": doc["type"], "data": doc["data"]}
            })
        elif doc.get("type") == "application/pdf":
            content.append({
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": doc["data"]}
            })

    if user_context:
        content.append({"type": "text", "text": f"Additional context: {user_context}"})

    content.append({
        "type": "text",
        "text": "Analyze this claim and respond ONLY with valid JSON. No markdown, no preamble."
    })

    messages.append({"role": "user", "content": content})
    analysis = _call_claude(messages)
    return analysis, messages


def refine_claim(analysis: dict, user_message: str,
                 conversation_history: list) -> tuple[dict, list]:
    messages = list(conversation_history)
    messages.append({"role": "assistant", "content": json.dumps(analysis)})
    messages.append({
        "role": "user",
        "content": user_message + "\n\nRespond ONLY with updated valid JSON. No markdown."
    })
    updated = _call_claude(messages)
    return updated, messages


# ── Dashboard stats helper ─────────────────────────────────────────────────

def _dashboard_stats():
    all_claims = Claim.query.order_by(Claim.created_at.desc()).all()
    total = len(all_claims)
    resolved = [c for c in all_claims if c.status == "resolved" and c.recovered_cents]
    total_recovered = sum(c.recovered_cents for c in resolved)
    pending = len([c for c in all_claims if c.status in ("analyzed", "approved", "submitted")])
    return {
        "total":           total,
        "resolved_count":  len(resolved),
        "pending_count":   pending,
        "total_recovered": total_recovered,   # cents
    }


# ── Routes ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    session.clear()
    stats = _dashboard_stats()
    recent = Claim.query.order_by(Claim.created_at.desc()).limit(5).all()
    return render_template("index.html", stats=stats,
                           recent_claims=[c.to_dict() for c in recent])


@app.route("/dashboard")
def dashboard():
    claims = Claim.query.order_by(Claim.created_at.desc()).all()
    stats  = _dashboard_stats()
    return render_template("dashboard.html",
                           claims=[c.to_dict() for c in claims],
                           stats=stats)


@app.route("/claim/<int:claim_id>")
def view_claim(claim_id):
    claim = Claim.query.get_or_404(claim_id)
    return render_template("claim_detail.html", claim=claim.to_dict())


@app.route("/analyze", methods=["POST"])
def analyze():
    documents   = []
    user_context = request.form.get("context", "")
    ocr_used    = False
    ocr_pages   = 0
    doc_names   = []

    for f in request.files.getlist("documents"):
        if not f.filename:
            continue
        file_bytes = f.read()
        mime       = f.content_type or "application/octet-stream"
        doc_names.append(f.filename)

        doc = process_document(file_bytes, mime, f.filename)
        if doc.get("ocr_used"):
            ocr_used  = True
            ocr_pages += doc.get("ocr_pages", 0)
        documents.append(doc)

    pasted = request.form.get("pasted_text", "").strip()
    if pasted:
        documents.append({"name": "Pasted content", "type": "text/plain", "text": pasted,
                          "ocr_used": False, "ocr_pages": 0})
        doc_names.append("pasted_text")

    if not documents:
        return jsonify({"error": "Please upload at least one document or paste your text."}), 400

    # Create a DB record immediately so we have an ID
    claim = Claim(status="pending", document_names=doc_names,
                  ocr_used=ocr_used, ocr_pages=ocr_pages)
    db.session.add(claim)
    db.session.commit()

    try:
        analysis, history = analyze_claim(documents, user_context)
        claim.update_from_analysis(analysis)
        db.session.commit()

        session["claim_id"] = claim.id
        session["history"]  = history
        session["analysis"] = analysis
        return jsonify({"analysis": analysis, "claim_id": claim.id,
                        "ocr_used": ocr_used, "ocr_pages": ocr_pages})
    except Exception as e:
        log.exception("Analysis failed")
        claim.status = "error"
        db.session.commit()
        return jsonify({"error": str(e)}), 500


@app.route("/refine", methods=["POST"])
def refine():
    data         = request.get_json()
    user_message = data.get("message", "")
    analysis     = session.get("analysis", {})
    history      = session.get("history", [])
    claim_id     = session.get("claim_id")

    if not user_message:
        return jsonify({"error": "No message provided"}), 400

    try:
        updated, updated_history = refine_claim(analysis, user_message, history)

        if claim_id:
            claim = Claim.query.get(claim_id)
            if claim:
                claim.update_from_analysis(updated)
                db.session.commit()

        session["history"]  = updated_history
        session["analysis"] = updated
        return jsonify({"analysis": updated})
    except Exception as e:
        log.exception("Refine failed")
        return jsonify({"error": str(e)}), 500


@app.route("/approve", methods=["POST"])
def approve():
    analysis = session.get("analysis", {})
    claim_id = session.get("claim_id")
    if not analysis:
        return jsonify({"error": "No active claim"}), 400

    if claim_id:
        claim = Claim.query.get(claim_id)
        if claim:
            claim.status = "approved"
            db.session.commit()

    return jsonify({
        "approved":    True,
        "claim_id":    claim_id,
        "claim_package": {
            "letter":      analysis.get("draft_letter"),
            "submit_to":   analysis.get("submit_to"),
            "amount":      analysis.get("amount_at_stake"),
            "legal_basis": analysis.get("legal_basis"),
        }
    })


@app.route("/claim/<int:claim_id>/resolve", methods=["POST"])
def resolve_claim(claim_id):
    """Mark a claim as resolved and record how much was recovered."""
    claim = Claim.query.get_or_404(claim_id)
    data  = request.get_json()

    recovered_str  = data.get("recovered", "0")
    resolution_note = data.get("note", "")

    # Parse recovered amount
    import re
    digits = re.sub(r"[^\d.]", "", str(recovered_str))
    try:
        recovered_cents = int(round(float(digits) * 100))
    except ValueError:
        recovered_cents = 0

    claim.status          = "resolved"
    claim.resolved_at     = datetime.now(timezone.utc)
    claim.recovered_cents = recovered_cents
    claim.resolution_note = resolution_note
    db.session.commit()

    return jsonify({"ok": True, "claim": claim.to_dict()})


@app.route("/api/claims")
def api_claims():
    """JSON endpoint for dashboard data."""
    claims = Claim.query.order_by(Claim.created_at.desc()).all()
    stats  = _dashboard_stats()
    return jsonify({"claims": [c.to_dict() for c in claims], "stats": stats})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
