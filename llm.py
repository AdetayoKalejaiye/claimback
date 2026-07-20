"""
llm.py — talk to Claude through the locally-installed `claude` CLI (Claude Code)
instead of the HTTP API. This uses the user's existing Claude *subscription*
auth on this machine, so NO ANTHROPIC_API_KEY is required.

We invoke:  claude -p --output-format json --model <m> --append-system-prompt <s>
with the user prompt piped on stdin, and read the `.result` field from the JSON
envelope. Attached images/PDFs are written to temp files and read by the CLI's
Read tool (pre-allowed so it never prompts).
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile

# Full model ids → CLI aliases (aliases are the most robust in the CLI).
_MODEL_ALIAS = {
    "claude-opus-4-8": "opus",
    "claude-sonnet-5": "sonnet",
    "claude-haiku-4-5-20251001": "haiku",
}

_EXT = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg", "image/jpg": ".jpg",
    "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp",
}


class LLMError(Exception):
    pass


def _model_arg(model: str) -> str:
    return _MODEL_ALIAS.get(model, model)


def write_temp_file(b64_data: str, mime: str, name: str = "") -> str:
    """Decode base64 doc bytes to a temp file the CLI can Read; returns path."""
    fd, path = tempfile.mkstemp(suffix=_EXT.get(mime, ""), prefix="claimback_")
    with os.fdopen(fd, "wb") as fh:
        fh.write(base64.b64decode(b64_data))
    return path


def generate_json(system_prompt: str, user_text: str, model: str = "opus",
                  file_paths: list | None = None, timeout: int = 300,
                  _expect_list: bool = False):
    """Run one headless `claude -p` call and return the parsed JSON result
    (a dict, or a list when `_expect_list` is True)."""
    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--model", _model_arg(model),
        "--system-prompt", system_prompt,   # fully replace the default CC persona
    ]
    if file_paths:
        # Pre-allow Read so the CLI can open the attached files without prompting.
        cmd += ["--allowedTools", "Read"]

    try:
        proc = subprocess.run(cmd, input=user_text, capture_output=True,
                              text=True, timeout=timeout)
    except FileNotFoundError:
        raise LLMError("The 'claude' CLI was not found on PATH. Install Claude "
                       "Code, or set ANTHROPIC_API_KEY to use the API instead.")
    except subprocess.TimeoutExpired:
        raise LLMError("Claude CLI timed out.")

    if proc.returncode != 0:
        raise LLMError(proc.stderr.strip() or f"claude CLI exited {proc.returncode}")

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise LLMError(f"Could not parse Claude CLI output: {proc.stdout[:200]}")

    if envelope.get("is_error"):
        raise LLMError(envelope.get("result") or "Claude CLI reported an error.")

    return _parse_json(envelope.get("result", "") or "", expect_list=_expect_list)


def map_dynamic(fields: list, analysis: dict, model: str = "sonnet") -> list:
    """Given live-scraped form fields and the extracted claim data, ask Claude
    which fields to fill and with what values. Returns [{selector, value, label}]."""
    import json as _json
    fields_block = _json.dumps(fields, indent=2)
    prompt = (
        "You are filling a company's real online claim form on behalf of a user.\n\n"
        "EXTRACTED CLAIM DATA (JSON):\n"
        f"{_json.dumps(analysis, indent=2)}\n\n"
        "The form currently shows these fields (scraped live from the page):\n"
        f"{fields_block}\n\n"
        "Return ONLY a JSON array of the fields you can confidently fill, each as "
        '{\"selector\": <exact selector from the list>, \"value\": <string>}. '
        "Match values to each field's type/label; for a 'select' field, value MUST be "
        "one of its listed options. Omit any field you'd be guessing at. Dates as the "
        "format the field seems to expect (default yyyy-mm-dd); amounts as digits.\n"
        "If the form asks for the claimant's mailing address and the claim doesn't give "
        "one, use: address 353 Jane Stanford Way, city Palo Alto, state California "
        "(or CA), ZIP 94305, country United States.\n"
        "Respond ONLY with the JSON array. No markdown, no preamble."
    )
    result = generate_json(
        "You map claim data onto live web-form fields and return only a valid JSON array.",
        prompt, model=model,
        _expect_list=True,
    )
    out = []
    valid = {f.get("selector") for f in fields}
    for m in (result or []):
        sel, val = m.get("selector"), m.get("value")
        if sel in valid and val not in (None, ""):
            out.append({"selector": sel, "value": str(val),
                        "label": next((f.get("label") for f in fields
                                       if f.get("selector") == sel), sel)})
    return out


def _parse_json(text: str, expect_list: bool = False):
    """Extract a JSON object or array from the model's text (tolerate fences / preamble)."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1].rsplit("```", 1)[0].strip() if "\n" in t else t.strip("`")
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    open_ch, close_ch = ("[", "]") if expect_list else ("{", "}")
    i, j = t.find(open_ch), t.rfind(close_ch)
    if i != -1 and j > i:
        return json.loads(t[i:j + 1])
    raise LLMError("Model did not return valid JSON.")
