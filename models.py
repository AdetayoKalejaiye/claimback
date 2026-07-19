from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Claim(db.Model):
    __tablename__ = "claims"

    id            = db.Column(db.Integer, primary_key=True)
    created_at    = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at    = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                              onupdate=lambda: datetime.now(timezone.utc))

    # Status lifecycle: pending → analyzed → approved → submitted → resolved → closed
    status        = db.Column(db.String(20), default="pending", nullable=False)

    # Core claim fields (populated after Claude analysis)
    claim_type    = db.Column(db.String(120))
    summary       = db.Column(db.Text)
    amount_str    = db.Column(db.String(50))   # raw string e.g. "$342.00" or "Unknown"
    amount_cents  = db.Column(db.Integer)       # parsed integer cents for math/sorting
    strength      = db.Column(db.String(20))    # Strong / Moderate / Weak
    strength_reason = db.Column(db.Text)

    # Dispute info
    dispute_primary   = db.Column(db.String(200))
    dispute_steps     = db.Column(db.JSON)       # list[str]
    dispute_escalation = db.Column(db.Text)
    legal_basis       = db.Column(db.JSON)       # list[str]

    # Submit target
    submit_name   = db.Column(db.String(200))
    submit_method = db.Column(db.String(50))
    submit_address = db.Column(db.Text)
    submit_form_url = db.Column(db.Text)

    # Generated content
    draft_letter  = db.Column(db.Text)

    # Resolution tracking (filled in by user later)
    resolved_at   = db.Column(db.DateTime)
    recovered_cents = db.Column(db.Integer)     # how much was actually recovered
    resolution_note = db.Column(db.Text)

    # Raw Claude JSON stored for re-rendering without re-analyzing
    raw_analysis  = db.Column(db.JSON)

    # OCR metadata
    ocr_used      = db.Column(db.Boolean, default=False)
    ocr_pages     = db.Column(db.Integer, default=0)

    # Documents list (filenames only — bytes not stored in DB)
    document_names = db.Column(db.JSON)

    def to_dict(self):
        return {
            "id":               self.id,
            "status":           self.status,
            "created_at":       self.created_at.isoformat() if self.created_at else None,
            "updated_at":       self.updated_at.isoformat() if self.updated_at else None,
            "claim_type":       self.claim_type,
            "summary":          self.summary,
            "amount_str":       self.amount_str,
            "amount_cents":     self.amount_cents,
            "strength":         self.strength,
            "strength_reason":  self.strength_reason,
            "dispute_primary":  self.dispute_primary,
            "dispute_steps":    self.dispute_steps,
            "dispute_escalation": self.dispute_escalation,
            "legal_basis":      self.legal_basis,
            "submit_name":      self.submit_name,
            "submit_method":    self.submit_method,
            "submit_address":   self.submit_address,
            "submit_form_url":  self.submit_form_url,
            "draft_letter":     self.draft_letter,
            "resolved_at":      self.resolved_at.isoformat() if self.resolved_at else None,
            "recovered_cents":  self.recovered_cents,
            "resolution_note":  self.resolution_note,
            "ocr_used":         self.ocr_used,
            "ocr_pages":        self.ocr_pages,
            "document_names":   self.document_names,
        }

    def update_from_analysis(self, analysis: dict):
        """Populate model fields from a Claude analysis JSON dict."""
        self.raw_analysis  = analysis
        self.claim_type    = analysis.get("claim_type")
        self.summary       = analysis.get("summary")
        self.strength      = analysis.get("strength")
        self.strength_reason = analysis.get("strength_reason")
        self.legal_basis   = analysis.get("legal_basis", [])
        self.draft_letter  = analysis.get("draft_letter")
        self.status        = "analyzed"

        dp = analysis.get("dispute_path", {})
        self.dispute_primary    = dp.get("primary")
        self.dispute_steps      = dp.get("steps", [])
        self.dispute_escalation = dp.get("escalation")

        st = analysis.get("submit_to", {})
        self.submit_name     = st.get("name")
        self.submit_method   = st.get("method")
        self.submit_address  = st.get("address")
        self.submit_form_url = st.get("form_url")

        raw_amount = analysis.get("amount_at_stake", "Unknown")
        self.amount_str   = raw_amount
        self.amount_cents = _parse_cents(raw_amount)

        self.updated_at = datetime.now(timezone.utc)


def _parse_cents(amount_str: str) -> int | None:
    """Parse '$1,234.56' → 123456 (cents). Returns None if unparseable."""
    if not amount_str or amount_str.lower() in ("unknown", "n/a", "—", ""):
        return None
    import re
    digits = re.sub(r"[^\d.]", "", amount_str)
    try:
        return int(round(float(digits) * 100))
    except ValueError:
        return None
