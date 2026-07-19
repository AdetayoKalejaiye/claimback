import os
import json
import base64
from flask import Flask, render_template, request, jsonify, session
import anthropic

app = Flask(__name__)
app.secret_key = os.urandom(24)

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

CLAIM_TYPES = {
    "medical_bill": "Medical Bill Dispute",
    "rejected_reimbursement": "Rejected Reimbursement",
    "damaged_delivery": "Damaged Delivery / Lost Package",
    "airline_disruption": "Airline Disruption (Delay/Cancellation)",
    "subscription_charge": "Unauthorized Subscription Charge",
    "warranty_rejection": "Warranty Claim Rejection",
    "insurance_eob": "Insurance Explanation of Benefits",
    "other": "Other Claim",
}

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
  "ready_to_submit": true/false
}"""


def encode_file(file_bytes, mime_type):
    """Encode file to base64 for Claude API."""
    return base64.standard_b64encode(file_bytes).decode("utf-8")


def analyze_claim(documents, user_context="", conversation_history=None):
    """Send documents to Claude for claim analysis."""
    messages = conversation_history or []

    content = []

    # Add each document
    for doc in documents:
        if doc["type"] in ["image/jpeg", "image/png", "image/gif", "image/webp"]:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": doc["type"],
                    "data": doc["data"],
                }
            })
        elif doc["type"] == "application/pdf":
            content.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": doc["data"],
                }
            })
        else:
            # Plain text / email content
            content.append({
                "type": "text",
                "text": f"Document ({doc.get('name', 'file')}):\n{doc.get('text', '')}"
            })

    if user_context:
        content.append({
            "type": "text",
            "text": f"Additional context from user: {user_context}"
        })

    content.append({
        "type": "text",
        "text": "Analyze this claim and respond ONLY with valid JSON as specified. No markdown, no explanation outside the JSON."
    })

    messages.append({"role": "user", "content": content})

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=messages,
    )

    raw = response.content[0].text.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    return json.loads(raw), messages


def refine_claim(analysis, user_message, conversation_history):
    """Continue the conversation to refine the claim."""
    messages = conversation_history.copy()

    # Add the previous analysis as assistant response
    messages.append({
        "role": "assistant",
        "content": json.dumps(analysis)
    })

    messages.append({
        "role": "user",
        "content": user_message + "\n\nRespond ONLY with updated valid JSON. No markdown."
    })

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=messages,
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    return json.loads(raw), messages


@app.route("/")
def index():
    session.clear()
    return render_template("index.html", claim_types=CLAIM_TYPES)


@app.route("/analyze", methods=["POST"])
def analyze():
    """Initial analysis of uploaded documents."""
    documents = []
    user_context = request.form.get("context", "")

    # Handle uploaded files
    for f in request.files.getlist("documents"):
        if f.filename:
            file_bytes = f.read()
            mime = f.content_type or "application/octet-stream"

            if mime in ["image/jpeg", "image/png", "image/gif", "image/webp", "application/pdf"]:
                documents.append({
                    "name": f.filename,
                    "type": mime,
                    "data": encode_file(file_bytes, mime),
                })
            else:
                # Try to decode as text
                try:
                    text = file_bytes.decode("utf-8")
                    documents.append({
                        "name": f.filename,
                        "type": "text/plain",
                        "text": text,
                    })
                except Exception:
                    pass

    # Handle pasted email/text content
    pasted_text = request.form.get("pasted_text", "").strip()
    if pasted_text:
        documents.append({
            "name": "Pasted content",
            "type": "text/plain",
            "text": pasted_text,
        })

    if not documents:
        return jsonify({"error": "Please upload at least one document or paste your text."}), 400

    try:
        analysis, history = analyze_claim(documents, user_context)
        session["history"] = history
        session["analysis"] = analysis
        return jsonify({"analysis": analysis})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/refine", methods=["POST"])
def refine():
    """Refine claim based on user answers."""
    data = request.get_json()
    user_message = data.get("message", "")
    analysis = session.get("analysis", {})
    history = session.get("history", [])

    if not user_message:
        return jsonify({"error": "No message provided"}), 400

    try:
        updated_analysis, updated_history = refine_claim(analysis, user_message, history)
        session["history"] = updated_history
        session["analysis"] = updated_analysis
        return jsonify({"analysis": updated_analysis})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/approve", methods=["POST"])
def approve():
    """User approves the claim for submission."""
    analysis = session.get("analysis", {})
    if not analysis:
        return jsonify({"error": "No active claim"}), 400

    # In production: auto-fill form, send email, etc.
    # For now: return the ready-to-submit package
    return jsonify({
        "approved": True,
        "claim_package": {
            "letter": analysis.get("draft_letter"),
            "submit_to": analysis.get("submit_to"),
            "amount": analysis.get("amount_at_stake"),
            "legal_basis": analysis.get("legal_basis"),
        }
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)
