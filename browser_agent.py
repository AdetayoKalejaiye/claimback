"""
browser_agent.py — drives a real, visible Chrome window to fill a dispute
portal, pause at submit, then submit on approval.

Threading: Playwright's sync API is thread-affine and cannot live in an asyncio
loop. Our fill→pause→submit arc spans two separate HTTP requests, so ONE
dedicated daemon thread owns the entire Playwright instance for the process.
Flask request threads never touch Playwright objects — they enqueue a callable
+ a concurrent.futures.Future and block on the result. Every Playwright object
is created and used only inside that one worker thread, so affinity always holds.

Run Flask with use_reloader=False so this singleton lives in exactly one process.
"""

from __future__ import annotations

import atexit
import queue
import threading
from concurrent.futures import Future

_SHUTDOWN = object()

# Field names that should be set at once rather than typed char-by-char
# (date pickers / number inputs don't play nicely with keystroke typing).
_FILL_ONLY = {"flight_date", "expense_total"}


class BrowserError(Exception):
    """Raised into the requesting thread when a browser action fails."""


class BrowserAgent:
    _instance: "BrowserAgent | None" = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self._queue: "queue.Queue" = queue.Queue()
        self._sessions: dict[int, dict] = {}   # claim_id -> {"browser":.., "page":..}
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="playwright-worker")
        self._thread.start()
        atexit.register(self._shutdown)

    # ── singleton accessor ────────────────────────────────────────────────
    @classmethod
    def instance(cls) -> "BrowserAgent":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    # ── worker thread loop (owns Playwright) ──────────────────────────────
    def _run(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            self._pw = pw
            while True:
                fn, fut = self._queue.get()
                if fn is _SHUTDOWN:
                    self._close_all()
                    break
                try:
                    fut.set_result(fn(pw))
                except Exception as e:            # noqa: BLE001 — surface to caller
                    fut.set_exception(e)

    def _dispatch(self, fn, timeout: float = 90.0):
        fut: Future = Future()
        self._queue.put((fn, fut))
        return fut.result(timeout=timeout)

    # ── public API (called from Flask request threads) ────────────────────
    def open_and_fill(self, claim_id: int, url: str, portal: dict,
                      values: dict) -> list[dict]:
        """Open headed Chrome, fill the portal from `values`, stop at submit.
        Returns the ordered list of {field, selector, value} actually filled."""
        return self._dispatch(
            lambda pw: self._do_fill(pw, claim_id, url, portal, values)
        )

    def submit(self, claim_id: int, submit_selector: str = "#submit-claim",
               reference_selector: str = "#reference-number") -> str:
        """Click the portal's submit button and return the reference number."""
        return self._dispatch(
            lambda pw: self._do_submit(pw, claim_id, submit_selector, reference_selector))

    def close(self, claim_id: int):
        self._dispatch(lambda pw: self._do_close(pw, claim_id), timeout=30.0)

    # ── dynamic (real-site) API ───────────────────────────────────────────
    def open_and_scrape(self, claim_id: int, url: str,
                        dismiss_selectors=None) -> list[dict]:
        """Open headed Chrome at `url`, dismiss banners, and return the live
        form fields [{selector, label, name, type, options}]. Keeps page open."""
        return self._dispatch(
            lambda pw: self._do_scrape(pw, claim_id, url, dismiss_selectors or []),
            timeout=120.0)

    def fill_scraped(self, claim_id: int, mappings: list) -> list[dict]:
        """Fill the already-open page from [{selector, value}] and stop at submit."""
        return self._dispatch(lambda pw: self._do_fill_scraped(pw, claim_id, mappings))

    def scrape_open(self, claim_id: int) -> list[dict]:
        """Re-scrape the CURRENT page of an already-open session (next wizard step)."""
        return self._dispatch(lambda pw: self._do_scrape_open(pw, claim_id), timeout=60.0)

    def advance(self, claim_id: int) -> dict:
        """Click a 'Continue/Next' button to move to the next wizard step. Does NOT
        click a final submit. Returns {advanced: bool, at_submit: bool}."""
        return self._dispatch(lambda pw: self._do_advance(pw, claim_id), timeout=60.0)

    # ── implementations (run ONLY on the worker thread) ───────────────────
    def _do_fill(self, pw, claim_id, url, portal, values):
        # One active demo window: close any prior session first.
        self._close_session(claim_id)

        browser = pw.chromium.launch(channel="chrome", headless=False,
                                     slow_mo=350)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        self._sessions[claim_id] = {"browser": browser, "page": page,
                                    "context": context}

        page.goto(url, wait_until="domcontentloaded")
        page.bring_to_front()

        # Best-effort: dismiss cookie/consent banners on real sites.
        for sel in portal.get("dismiss_selectors", []):
            try:
                page.locator(sel).first.click(timeout=2500)
            except Exception:
                pass

        fields = portal.get("fields", {})
        selects = portal.get("selects", {})
        submit_selector = portal.get("submit_selector", "#submit-claim")
        filled: list[dict] = []

        for name, selector in fields.items():
            value = values.get(name)
            if value in (None, ""):
                continue
            value = str(value)
            try:
                loc = page.locator(selector)
                loc.scroll_into_view_if_needed(timeout=5000)
                if name in selects:
                    page.select_option(selector, value)
                elif name in _FILL_ONLY:
                    loc.click()
                    loc.fill(value)
                else:
                    loc.click()
                    page.type(selector, value, delay=45)  # visible typing
                filled.append({"field": name, "selector": selector, "value": value})
            except Exception:
                # Skip a field that isn't present rather than aborting the demo.
                continue

        # Bring the submit button into view but DO NOT click it.
        try:
            page.locator(submit_selector).first.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        return filled

    def _do_submit(self, pw, claim_id, submit_selector="#submit-claim",
                   reference_selector="#reference-number"):
        sess = self._sessions.get(claim_id)
        if not sess:
            raise BrowserError("No open portal for this claim — run auto-fill again.")
        page = sess["page"]
        if page.is_closed():
            self._close_session(claim_id)
            raise BrowserError("The portal window was closed — run auto-fill again.")
        try:
            page.locator(submit_selector).first.click()
            page.wait_for_selector(reference_selector, timeout=15000)
            reference = page.inner_text(reference_selector).strip()
        except Exception as e:               # noqa: BLE001
            raise BrowserError(f"Submission failed: {e}")
        return reference

    def _do_close(self, pw, claim_id):
        self._close_session(claim_id)
        return True

    # ── dynamic (real-site) implementations ───────────────────────────────
    def _do_scrape(self, pw, claim_id, url, dismiss_selectors):
        self._close_session(claim_id)
        browser = pw.chromium.launch(channel="chrome", headless=False, slow_mo=250)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        self._sessions[claim_id] = {"browser": browser, "page": page,
                                    "context": context}

        page.goto(url, wait_until="domcontentloaded")
        page.bring_to_front()
        # Give the SPA time to render + settle network before scraping.
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        try:
            page.wait_for_timeout(2500)
        except Exception:
            pass

        for sel in dismiss_selectors:
            try:
                page.locator(sel).first.click(timeout=2500)
            except Exception:
                pass
        try:
            page.wait_for_timeout(800)  # let any post-consent content render
        except Exception:
            pass

        # Enumerate visible form controls (incl. custom React widgets).
        return self._enumerate(page)

    def _enumerate(self, page):
        """Return the current page's visible form fields (shared by all steps)."""
        fields = page.evaluate(r"""
        () => {
          const css = (el) => {
            if (el.id) return '#' + CSS.escape(el.id);
            // Angular/React reactive forms: the unique key is formcontrolname
            // on an ancestor wrapper (the raw input often has id="" and a shared
            // name like "textfield"). Build [formcontrolname="x"] <tag>.
            let a = el;
            while (a && a !== document.body) {
              const fc = a.getAttribute && a.getAttribute('formcontrolname');
              if (fc) return '[formcontrolname="' + fc + '"] ' + el.tagName.toLowerCase();
              a = a.parentElement;
            }
            if (el.getAttribute('data-testid'))
              return '[data-testid="' + el.getAttribute('data-testid') + '"]';
            const nm = el.getAttribute('name');
            if (nm && document.querySelectorAll('[name="' + CSS.escape(nm) + '"]').length === 1)
              return el.tagName.toLowerCase() + '[name="' + nm + '"]';
            const same = [...document.querySelectorAll(el.tagName)];
            return el.tagName.toLowerCase() + ':nth-of-type(' + (same.indexOf(el) + 1) + ')';
          };
          const labelFor = (el) => {
            if (el.labels && el.labels[0]) return el.labels[0].innerText.trim();
            const ab = el.getAttribute('aria-labelledby');
            if (ab) {
              const t = ab.split(' ').map(id => { const n = document.getElementById(id);
                return n ? n.innerText.trim() : ''; }).filter(Boolean).join(' ');
              if (t) return t;
            }
            if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
            if (el.placeholder) return el.placeholder;
            const l = el.closest('label'); if (l) return l.innerText.trim();
            // formcontrolname is a good semantic hint when labels are missing
            let a = el;
            while (a && a !== document.body) {
              const fc = a.getAttribute && a.getAttribute('formcontrolname');
              if (fc) return fc;
              a = a.parentElement;
            }
            const prev = el.previousElementSibling;
            if (prev && prev.innerText) return prev.innerText.trim();
            return el.getAttribute('name') || '';
          };
          const sel = 'input, select, textarea, [contenteditable=""], [contenteditable="true"], ' +
                      '[role="textbox"], [role="combobox"], [role="spinbutton"]';
          const out = [];
          const seen = new Set();
          for (const el of document.querySelectorAll(sel)) {
            const tag = el.tagName.toLowerCase();
            let t = (el.type || tag).toLowerCase();
            if (['hidden','submit','button','search','image','reset'].includes(t)) continue;
            const r = el.getBoundingClientRect();
            if (r.width === 0 && r.height === 0) continue;  // skip invisible
            const isCombo = el.getAttribute('role') === 'combobox';
            if (tag === 'select' || isCombo) t = 'select';
            else if (el.isContentEditable) t = 'contenteditable';
            const s = css(el);
            if (seen.has(s)) continue; seen.add(s);
            const f = { selector: s, name: el.getAttribute('name') || '',
                        type: t, label: (labelFor(el) || '').slice(0, 80) };
            if (tag === 'select')
              f.options = [...el.options].map(o => o.value).filter(Boolean);
            else if (isCombo) {
              // custom dropdown: options live in a sibling [role=option] list
              const root = el.closest('[class*="dropdown"],[class*="select"]') || el.parentElement;
              f.options = [...(root ? root.querySelectorAll('[role=option]') : [])]
                .map(o => (o.getAttribute('data-label') || o.innerText || '').trim())
                .filter(Boolean).slice(0, 60);
            }
            out.push(f);
          }
          // Custom day-grid calendars (Angular) aren't standard inputs — capture
          // them by their formcontrolname wrapper so the mapper can supply a date.
          for (const cal of document.querySelectorAll('idp-molecule-calendar-dropdown[formcontrolname]')) {
            const fc = cal.getAttribute('formcontrolname');
            const s = '[formcontrolname="' + fc + '"]';
            if (seen.has(s)) continue; seen.add(s);
            const tb = cal.querySelector('[role=textbox]');
            out.push({ selector: s, name: fc, type: 'date',
                       label: ((tb && tb.innerText) || fc).trim().slice(0, 80) });
          }
          return out.slice(0, 60);
        }
        """)
        return fields or []

    def _do_scrape_open(self, pw, claim_id):
        sess = self._sessions.get(claim_id)
        if not sess:
            raise BrowserError("No open page — run auto-fill again.")
        page = sess["page"]
        if page.is_closed():
            self._close_session(claim_id)
            raise BrowserError("The window was closed — run auto-fill again.")
        return self._enumerate(page)

    def _do_advance(self, pw, claim_id):
        """Click a Continue/Next button (never a final submit) to move to the next
        wizard step. Returns {'advanced': bool, 'at_submit': bool}."""
        sess = self._sessions.get(claim_id)
        if not sess:
            raise BrowserError("No open page — run auto-fill again.")
        page = sess["page"]
        if page.is_closed():
            self._close_session(claim_id)
            raise BrowserError("The window was closed — run auto-fill again.")

        # Look for an enabled button whose label says "continue"/"next" (advance),
        # vs. a final "submit"/"submit request" (stop, never click).
        info = page.evaluate(r"""
        () => {
          const btns = [...document.querySelectorAll('button, [role=button], input[type=submit]')];
          const vis = btns.filter(b => { const r = b.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && !b.disabled; });
          const txt = b => (b.innerText || b.value || b.getAttribute('aria-label') || '').trim().toLowerCase();
          const isNext = b => /continue|next|proceed/.test(txt(b));
          const isSubmit = b => /submit/.test(txt(b));
          const next = vis.find(isNext);
          return { hasNext: !!next, atSubmit: !next && vis.some(isSubmit) };
        }
        """)
        if not info.get("hasNext"):
            return {"advanced": False, "at_submit": bool(info.get("atSubmit"))}

        try:
            btn = page.locator(
                "button:has-text('Continue'), button:has-text('CONTINUE'), "
                "button:has-text('Next'), button:has-text('Proceed')").first
            btn.scroll_into_view_if_needed(timeout=3000)
            btn.click()
            page.wait_for_timeout(2000)  # let the next section render
            return {"advanced": True, "at_submit": False}
        except Exception:
            return {"advanced": False, "at_submit": False}

    def _do_fill_scraped(self, pw, claim_id, mappings):
        sess = self._sessions.get(claim_id)
        if not sess:
            raise BrowserError("No open page for this claim — run auto-fill again.")
        page = sess["page"]
        if page.is_closed():
            self._close_session(claim_id)
            raise BrowserError("The window was closed — run auto-fill again.")

        filled = []
        for m in mappings:
            selector, value = m.get("selector"), m.get("value")
            if not selector or value in (None, ""):
                continue
            value = str(value)
            try:
                loc = page.locator(selector).first
                loc.scroll_into_view_if_needed(timeout=4000)
                kind = loc.evaluate(
                    """e => {
                      const tag = e.tagName.toLowerCase();
                      if (tag.includes('calendar')) return 'calendar';
                      if (e.closest && e.closest('idp-autocomplete')) return 'autocomplete';
                      if (tag === 'select') return 'select';
                      if (e.getAttribute && e.getAttribute('role') === 'combobox') return 'combobox';
                      if (e.isContentEditable) return 'contenteditable';
                      return 'text';
                    }""")
                if kind == "select":
                    page.select_option(selector, value)
                elif kind == "calendar":
                    # Custom day-grid calendar: open, click the matching day
                    # (fall back to any selectable day), then confirm.
                    loc.locator("[role=button]").first.click()
                    page.wait_for_timeout(500)
                    target = ""
                    p = value.split("-")
                    if len(p) == 3:  # yyyy-mm-dd -> M/D/YYYY (calendar's data attr)
                        target = f"{int(p[1])}/{int(p[2])}/{p[0]}"
                    try:
                        if target:
                            page.locator(f'[data-calendar-date="{target}"]').first.click(timeout=1500)
                        else:
                            raise Exception("no target")
                    except Exception:
                        try:  # any selectable/active day so the field validates
                            page.locator('.calendar-table__active[tabindex="0"]').first.click(timeout=1500)
                        except Exception:
                            pass
                    for done in ("button:has-text('done')", "button:has-text('Done')"):
                        try:
                            page.locator(done).first.click(timeout=1200)
                            break
                        except Exception:
                            continue
                elif kind == "autocomplete":
                    # Type, wait for suggestions, pick the first one (or Enter).
                    loc.click()
                    try:
                        loc.fill("")
                    except Exception:
                        pass
                    loc.type(value, delay=90)
                    page.wait_for_timeout(700)
                    try:
                        page.locator("[role=option]").first.click(timeout=1500)
                    except Exception:
                        try:
                            loc.press("Enter")
                        except Exception:
                            pass
                elif kind == "contenteditable":
                    loc.click()
                    loc.evaluate("(e) => { e.textContent = ''; }")
                    loc.type(value, delay=35)
                elif kind == "combobox":
                    # Custom dropdown: open it, then click the option whose text
                    # or data-value matches the value (case-insensitive).
                    loc.click()
                    page.wait_for_timeout(350)  # let the option list render
                    v = value.strip().lower()
                    opt = page.locator(
                        f"[role=option][data-value='{value}'], "
                        f"[role=option][data-label='{value}']").first
                    try:
                        opt.click(timeout=1500)
                    except Exception:
                        # fall back to matching visible option text
                        clicked = page.evaluate(
                            """(v) => {
                              const opts = [...document.querySelectorAll('[role=option]')];
                              const hit = opts.find(o =>
                                (o.getAttribute('data-value')||'').toLowerCase() === v ||
                                (o.getAttribute('data-label')||'').toLowerCase() === v ||
                                (o.innerText||'').trim().toLowerCase() === v ||
                                (o.innerText||'').trim().toLowerCase().startsWith(v));
                              if (hit) { hit.click(); return true; } return false;
                            }""", v)
                        if not clicked:
                            continue  # couldn't match — skip this dropdown
                else:
                    loc.click()
                    try:
                        loc.fill("")
                    except Exception:
                        pass
                    loc.type(value, delay=40)
                filled.append({"field": m.get("label") or selector, "value": value})
            except Exception:
                continue  # skip fields that don't take the value; don't abort

        # Best-effort: scroll a likely submit button into view, but never click.
        for sel in ("button[type=submit]", "input[type=submit]",
                    "button:has-text('Submit')", "button:has-text('Continue')"):
            try:
                page.locator(sel).first.scroll_into_view_if_needed(timeout=1500)
                break
            except Exception:
                continue
        return filled


    # ── cleanup ───────────────────────────────────────────────────────────
    def _close_session(self, claim_id):
        sess = self._sessions.pop(claim_id, None)
        if not sess:
            return
        try:
            sess["browser"].close()
        except Exception:
            pass

    def _close_all(self):
        for cid in list(self._sessions):
            self._close_session(cid)

    def _shutdown(self):
        try:
            self._queue.put((_SHUTDOWN, Future()))
        except Exception:
            pass
