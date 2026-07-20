"""
portals.py — registry of dispute portals ClaimBack can auto-fill.

The engine is claim-type agnostic. Each entry maps a claim type to a concrete
web form: the Flask endpoint that renders it, the logical field name → CSS
selector contract, and any <select> fields with their allowed option values
(so the AI mapping step is constrained to real options).

Airline is the flagship demo portal. Adding another claim type later = add a
template + one entry here. No engine changes.
"""

PORTALS = {
    "airline": {
        "label": "SkyClaim Airlines — Reimbursement Center",
        "url_endpoint": "portal",  # url_for target -> GET /portal
        "fields": {
            "passenger_name":      "#passenger_name",
            "flight_number":       "#flight_number",
            "route":               "#route",
            "flight_date":         "#flight_date",
            "delay_reason":        "#delay_reason",
            "expense_total":       "#expense_total",
            "expense_description": "#expense_description",
            "claim_narrative":     "#claim_narrative",
        },
        "selects": {
            "delay_reason": [
                "technical", "weather", "staffing",
                "extraordinary_circumstances", "other",
            ],
        },
        # Human-readable field descriptions used to prompt the mapping model.
        "descriptions": {
            "passenger_name":      "Full name of the passenger on the booking",
            "flight_number":       "Flight number, e.g. BA249",
            "route":               "Route as 'ORIGIN → DESTINATION' airport codes, e.g. 'LHR → JFK'",
            "flight_date":         "Date of the disrupted flight in ISO format yyyy-mm-dd",
            "delay_reason":        "Cause of the disruption (choose the closest option value)",
            "expense_total":       "Total out-of-pocket amount claimed, digits only e.g. 342.00",
            "expense_description": "Short list of the expenses (hotel, meals, transport)",
            "claim_narrative":     "2-4 sentence plain summary of what happened and why reimbursement is owed",
        },
    },
    # future: "medical": {...}, "subscription": {...} — drop-in, no engine change.
}

# ── Real-site template ───────────────────────────────────────────────────────
# To fill an ACTUAL airline/claims website instead of the mock portal, add an
# entry like the one below and point `portal_for_claim_type` at it (e.g. rename
# it to "airline" or make the fuzzy match hit it first).
#
# Extra keys a real portal supports beyond the mock:
#   "url"              absolute URL to open (instead of "url_endpoint")
#   "dismiss_selectors" list of cookie/consent buttons to click first (best-effort)
#   "submit_selector"  the real submit button (defaults to "#submit-claim")
#   "auto_submit"      MUST be False for real sites — fill only, never file a claim
#
# Get the selectors by opening the real form in Chrome → right-click a field →
# Inspect → copy its id/name → write a CSS selector (e.g. "#firstName",
# "input[name='flightNumber']").
#
# PORTALS["airline_real"] = {
#     "label": "British Airways — EU261 claim",
#     "url": "https://www.britishairways.com/travel/eu-compensation/...",
#     "auto_submit": False,                       # fill only — do NOT submit
#     "dismiss_selectors": ["#consent-accept", "button[aria-label='Accept cookies']"],
#     "submit_selector": "#realSubmitButton",
#     "fields": {
#         "passenger_name": "#firstName",
#         "flight_number":  "input[name='flightNumber']",
#         # ... map each logical field to the REAL selector you inspected ...
#     },
#     "selects": { },
#     "descriptions": { },  # copy from the airline entry as needed
# }

# Which portal to use when the claim type doesn't map to a specific one.
DEFAULT_PORTAL = "airline"


def portal_for_claim_type(claim_type: str | None) -> dict:
    """Pick a portal by (fuzzy) claim type. Falls back to the default."""
    key = (claim_type or "").lower()
    for name, cfg in PORTALS.items():
        if name in key:
            return cfg
    return PORTALS[DEFAULT_PORTAL]


# ── Hardcoded company detection for the text box (Delta + FedEx only) ────────
# Best-effort cookie/consent dismiss selectors common on these sites.
_COMMON_DISMISS = [
    "#onetrust-accept-btn-handler",
    "button#truste-consent-button",
    "button[aria-label*='Accept']",
    "button:has-text('Accept All')",
    "button:has-text('Accept all')",
    "button:has-text('Accept')",
    "button:has-text('I Agree')",
]


def detect_target(text: str):
    """Map free text (e.g. 'Delta reimbursement') to a real company form.
    Returns {company, url, dismiss_selectors} or None (→ caller uses the mock)."""
    t = (text or "").lower()
    if "delta" in t:
        if "refund" in t:
            url = "https://www.delta.com/refund-form/"
        elif "bag" in t or "baggage" in t or "luggage" in t:
            url = "https://www.delta.com/bag-claim"
        else:
            url = "https://www.delta.com/reimbursement/"
        return {"company": "Delta Air Lines", "url": url,
                "dismiss_selectors": _COMMON_DISMISS}
    if "fedex" in t or "fed ex" in t:
        return {"company": "FedEx",
                "url": "https://www.fedex.com/en-us/customer-support/claims/start.html",
                "dismiss_selectors": _COMMON_DISMISS}
    return None
