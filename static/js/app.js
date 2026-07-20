/* =============================================
   ClaimBack — Frontend Logic
============================================= */

const state = {
  files: [],
  analysis: null,
  claimPackage: null,
};

/* ── DOM refs ── */
const dropZone    = document.getElementById("drop-zone");
const fileInput   = document.getElementById("file-input");
const fileList    = document.getElementById("file-list");
const pastedText  = document.getElementById("pasted-text");
const userContext = document.getElementById("user-context");
const btnAnalyze  = document.getElementById("btn-analyze");

const uploadSection  = document.getElementById("upload-section");
const loadingState   = document.getElementById("loading-state");
const resultsPanel   = document.getElementById("results-panel");

/* ── Drag & Drop ── */
["dragenter", "dragover"].forEach(e =>
  dropZone.addEventListener(e, ev => { ev.preventDefault(); dropZone.classList.add("drag-over"); })
);

["dragleave", "drop"].forEach(e =>
  dropZone.addEventListener(e, ev => { ev.preventDefault(); dropZone.classList.remove("drag-over"); })
);

dropZone.addEventListener("drop", ev => {
  const files = Array.from(ev.dataTransfer.files);
  addFiles(files);
});

dropZone.addEventListener("click", e => {
  if (e.target !== fileInput && !e.target.classList.contains("file-chip") && !e.target.closest(".file-chip")) {
    fileInput.click();
  }
});

fileInput.addEventListener("change", () => {
  addFiles(Array.from(fileInput.files));
  fileInput.value = "";
});

function addFiles(files) {
  files.forEach(f => {
    if (!state.files.find(x => x.name === f.name)) {
      state.files.push(f);
    }
  });
  renderFileList();
}

function removeFile(name) {
  state.files = state.files.filter(f => f.name !== name);
  renderFileList();
}

function renderFileList() {
  fileList.innerHTML = "";
  state.files.forEach(f => {
    const chip = document.createElement("div");
    chip.className = "file-chip";
    chip.innerHTML = `<span>${fileIcon(f)} ${f.name}</span><button onclick="removeFile('${f.name}')" title="Remove">✕</button>`;
    fileList.appendChild(chip);
  });
}

function fileIcon(f) {
  if (f.type.startsWith("image/")) return "🖼";
  if (f.type === "application/pdf") return "📄";
  return "📧";
}

/* ── Analyze ── */
btnAnalyze.addEventListener("click", analyze);

/* ── Hero search box: type the situation → analyze → auto-open the company form ── */
const heroInput = document.getElementById("hero-input");
const heroGo = document.getElementById("hero-go");

async function runFromText(text) {
  text = (text || "").trim();
  if (!text) { heroInput.focus(); return; }
  state.analysis = null;
  state.files = [];
  renderFileList();
  pastedText.value = text;       // analyze() reads this
  userContext.value = "";
  await analyze();               // sets state.analysis + renders results on success
  if (!state.analysis) return;   // analysis failed (alert already shown)

  // Remember the named company, but DO NOT act yet — the user reviews/edits the
  // drafted claim first and must click "Approve & Auto-fill" to start the browser.
  state.pendingCompany = /delta|fedex|fed ex/i.test(text) ? text : "";
}

heroGo.addEventListener("click", () => runFromText(heroInput.value));
heroInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); runFromText(heroInput.value); }
});
document.querySelectorAll(".hero-pill").forEach((pill) => {
  pill.addEventListener("click", () => {
    heroInput.value = pill.dataset.q || pill.textContent;
    heroInput.focus();
  });
});
document.getElementById("hero-demo-link").addEventListener("click", (e) => {
  e.preventDefault();
  document.getElementById("btn-demo").click();
});

/* ── One-click airline demo (loads the canned EU261 packet) ── */
const DEMO_FILES = [
  "/static/demo/cancellation_email.txt",
  "/static/demo/hotel_receipt.txt",
];
document.getElementById("btn-demo").addEventListener("click", async () => {
  try {
    const files = await Promise.all(DEMO_FILES.map(async (url) => {
      const res = await fetch(url);
      const text = await res.text();
      const name = url.split("/").pop();
      return new File([text], name, { type: "text/plain" });
    }));
    state.files = files;
    renderFileList();
    userContext.value =
      "Flight was cancelled the night before departure; I had to pay for a hotel and meals.";
    analyze();
  } catch (e) {
    alert("Couldn't load the demo files.");
  }
});

async function analyze() {
  if (state.files.length === 0 && !pastedText.value.trim()) {
    alert("Please upload a document or paste your text first.");
    return;
  }

  const formData = new FormData();
  state.files.forEach(f => formData.append("documents", f));
  formData.append("pasted_text", pastedText.value.trim());
  formData.append("context", userContext.value.trim());

  showLoading("Reading your documents...");

  try {
    const res = await fetch("/analyze", { method: "POST", body: formData });
    const data = await res.json();

    if (data.error) { alert("Error: " + data.error); hideLoading(); return; }

    state.analysis  = data.analysis;
    state.claim_id  = data.claim_id;

    // Show OCR notice if scanned docs were processed
    if (data.ocr_used) {
      const notice = document.getElementById("ocr-notice");
      document.getElementById("ocr-notice-text").textContent =
        `📷 OCR ran on ${data.ocr_pages} page(s) — scanned text extracted successfully`;
      notice.classList.remove("hidden");
    }

    renderResults(data.analysis);
  } catch (e) {
    alert("Something went wrong. Please try again.");
    hideLoading();
  }
}

/* ── Refine ── */
document.getElementById("btn-refine").addEventListener("click", async () => {
  const reply = document.getElementById("user-reply").value.trim();
  if (!reply) return;

  showLoading("Updating your claim...");

  const res = await fetch("/refine", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: reply }),
  });
  const data = await res.json();

  if (data.error) { alert("Error: " + data.error); hideLoading(); return; }

  state.analysis = data.analysis;
  renderResults(data.analysis);
  document.getElementById("user-reply").value = "";
});

/* ── Approve → choose where to file (company box or safe demo) ── */
document.getElementById("btn-approve").addEventListener("click", async () => {
  const res = await fetch("/approve", { method: "POST" });
  const data = await res.json();
  if (data.error) { alert("Error: " + data.error); return; }
  state.claimPackage = data.claim_package;

  document.getElementById("approval-bar").classList.add("hidden");
  document.getElementById("escalate-panel").classList.add("hidden");
  const panel = document.getElementById("autofill-panel");
  panel.classList.remove("hidden");
  document.getElementById("autofill-review-wrap").classList.add("hidden");
  panel.scrollIntoView({ behavior: "smooth" });

  // If a company was named up front (Delta/FedEx), approving fills that real
  // site directly. Otherwise show the chooser to pick a target.
  if (state.pendingCompany) {
    document.getElementById("autofill-chooser").classList.add("hidden");
    runAutofill(state.pendingCompany);
  } else {
    document.getElementById("autofill-chooser").classList.remove("hidden");
    document.getElementById("autofill-title").textContent = "Where should I file this?";
    document.getElementById("autofill-sub").textContent =
      "Type the company, or use the safe demo portal.";
    document.getElementById("company-input").focus();
  }
});

/* Fill a form (company text → real site, or empty → mock SkyClaim) */
async function runAutofill(company) {
  document.getElementById("autofill-chooser").classList.add("hidden");
  document.getElementById("autofill-review-wrap").classList.add("hidden");
  document.getElementById("autofill-title").textContent =
    company ? "Opening the company's site…" : "Filling the demo portal…";
  document.getElementById("autofill-sub").textContent =
    "Watch the Chrome window — ClaimBack is completing the claim form for you.";

  const fillRes = await fetch("/autofill", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ company: company || "" }),
  });
  const fillData = await fillRes.json();

  if (fillData.error) {
    document.getElementById("autofill-title").textContent = "Couldn't auto-fill";
    document.getElementById("autofill-sub").textContent = fillData.error;
    document.getElementById("autofill-chooser").classList.remove("hidden");
    return;
  }

  document.getElementById("autofill-title").textContent =
    "Form filled — " + (fillData.portal_label || "portal");
  document.getElementById("autofill-sub").textContent = fillData.auto_submit
    ? "The claim is entered and waiting at the submit button. Review and approve to submit."
    : "Filled on the real site — it will NOT be submitted automatically. Review below.";

  const table = document.getElementById("autofill-review");
  table.innerHTML = "";
  (fillData.filled || []).forEach(f => {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td class="review-field">${prettyField(f.field)}</td>` +
      `<td class="review-value">${escapeHtml(f.value)}</td>`;
    table.appendChild(tr);
  });
  if (!(fillData.filled || []).length) {
    table.innerHTML = `<tr><td colspan="2" class="review-field">No fields could be filled — the page may need login or be multi-step.</td></tr>`;
  }
  document.getElementById("btn-confirm-submit").querySelector("span").textContent =
    fillData.auto_submit ? "Confirm & Submit" : "Mark as filled";
  document.getElementById("autofill-review-wrap").classList.remove("hidden");
}

document.getElementById("btn-find-fill").addEventListener("click", () => {
  const company = document.getElementById("company-input").value.trim();
  if (!company) { alert("Type a company (e.g. Delta reimbursement)"); return; }
  runAutofill(company);
});
document.getElementById("btn-safe-demo").addEventListener("click", () => runAutofill(""));

/* ── Confirm & Submit (the approval gate → browser clicks submit) ── */
document.getElementById("btn-confirm-submit").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  btn.querySelector("span").textContent = "Submitting…";

  const res = await fetch("/autofill/submit", { method: "POST" });
  const data = await res.json();

  if (data.error) {
    alert("Error: " + data.error);
    btn.disabled = false;
    btn.querySelector("span").textContent = "Confirm & Submit";
    return;
  }

  // Transition to the confirmation panel with the portal's reference number.
  document.getElementById("autofill-panel").classList.add("hidden");
  const confirm = document.getElementById("confirmation-panel");
  confirm.classList.remove("hidden");

  const sub = state.claimPackage?.submit_to;
  if (data.manual) {
    // Real portal — filled only, not submitted (we never file a real claim).
    document.getElementById("confirm-instructions").textContent =
      data.message || "The form is filled in the browser — review and submit it manually.";
    document.querySelector("#confirmation-panel .confirm-headline").textContent =
      "Form Filled — Ready for You";
  } else {
    document.getElementById("confirm-instructions").textContent = sub
      ? `Submitted to ${sub.name || "the portal"}. We'll track this claim and remind you to follow up.`
      : "Your claim has been submitted. We'll track it and remind you to follow up.";
  }

  if (data.reference) {
    document.getElementById("confirm-reference-wrap").classList.remove("hidden");
    document.getElementById("confirm-reference").textContent = data.reference;
  }
  confirm.scrollIntoView({ behavior: "smooth" });
});

/* ── Escalate → draft an email to the right authority (manual send) ── */
document.getElementById("btn-escalate").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  const orig = btn.textContent;
  btn.textContent = "Drafting…";
  btn.disabled = true;

  const res = await fetch("/escalate", { method: "POST" });
  const data = await res.json();
  btn.textContent = orig;
  btn.disabled = false;
  if (data.error) { alert("Error: " + data.error); return; }

  document.getElementById("escalate-authority").textContent =
    "To authority: " + (data.authority || "the appropriate body");
  document.getElementById("escalate-to").textContent = data.to || "—";
  document.getElementById("escalate-subject").textContent = data.subject || "—";
  document.getElementById("escalate-body").textContent = data.body || "";

  const mailto = "mailto:" + encodeURIComponent(data.to || "") +
    "?subject=" + encodeURIComponent(data.subject || "") +
    "&body=" + encodeURIComponent(data.body || "");
  document.getElementById("btn-escalate-mail").setAttribute("href", mailto);

  const panel = document.getElementById("escalate-panel");
  panel.classList.remove("hidden");
  panel.scrollIntoView({ behavior: "smooth" });
});

document.getElementById("btn-escalate-copy").addEventListener("click", () => {
  const text = document.getElementById("escalate-body").textContent;
  copyToClipboard(text, document.getElementById("btn-escalate-copy"));
});

/* ── field-name / html helpers ── */
function prettyField(name) {
  return String(name).replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}
function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML;
}

/* ── Edit More ── */
document.getElementById("btn-edit-more").addEventListener("click", () => {
  const card = document.getElementById("card-questions");
  card.classList.remove("hidden");
  card.scrollIntoView({ behavior: "smooth" });
  document.getElementById("user-reply").focus();
});

/* ── Copy letter ── */
document.getElementById("btn-copy-letter").addEventListener("click", () => {
  const text = document.getElementById("res-letter").textContent;
  copyToClipboard(text, document.getElementById("btn-copy-letter"));
});

document.getElementById("btn-copy-all").addEventListener("click", () => {
  const pkg = state.claimPackage;
  if (!pkg) return;
  const full = [
    `CLAIMBACK DISPUTE PACKAGE`,
    `Amount: ${pkg.amount || "—"}`,
    `Submit to: ${pkg.submit_to?.name || "—"} via ${pkg.submit_to?.method || "—"}`,
    pkg.submit_to?.address ? `Address: ${pkg.submit_to.address}` : "",
    `\nLegal Basis:\n${(pkg.legal_basis || []).map(l => "• " + l).join("\n")}`,
    `\n--- CLAIM LETTER ---\n${pkg.letter}`,
  ].filter(Boolean).join("\n");
  copyToClipboard(full, document.getElementById("btn-copy-all"));
});

function copyToClipboard(text, btn) {
  navigator.clipboard.writeText(text).then(() => {
    const orig = btn.textContent;
    btn.textContent = "Copied!";
    btn.classList.add("copied");
    setTimeout(() => { btn.textContent = orig; btn.classList.remove("copied"); }, 2000);
  });
}

/* ── Render results ── */
function renderResults(a) {
  hideLoading();
  uploadSection.classList.add("hidden");
  resultsPanel.classList.remove("hidden");
  document.getElementById("confirmation-panel").classList.add("hidden");
  document.getElementById("autofill-panel").classList.add("hidden");
  document.getElementById("escalate-panel").classList.add("hidden");
  document.getElementById("approval-bar").classList.remove("hidden");

  // Overview
  document.getElementById("res-claim-type").textContent = a.claim_type || "Unknown Claim";
  document.getElementById("res-summary").textContent = a.summary || "";
  document.getElementById("res-amount").textContent = a.amount_at_stake || "—";
  document.getElementById("res-path").textContent = a.dispute_path?.primary || "—";
  document.getElementById("res-submit-to").textContent = a.submit_to?.name || "—";

  // Strength badge
  const badge = document.getElementById("res-strength-badge");
  const strength = (a.strength || "Moderate").toLowerCase();
  badge.className = "strength-badge " + strength;
  document.getElementById("res-strength").textContent = a.strength || "—";
  badge.title = a.strength_reason || "";

  // Legal basis
  const legalList = document.getElementById("res-legal-list");
  legalList.innerHTML = "";
  (a.legal_basis || []).forEach(law => {
    const li = document.createElement("li");
    li.textContent = law;
    legalList.appendChild(li);
  });

  // Steps
  const stepsList = document.getElementById("res-steps-list");
  stepsList.innerHTML = "";
  (a.dispute_path?.steps || []).forEach(step => {
    const li = document.createElement("li");
    li.textContent = step;
    stepsList.appendChild(li);
  });

  // Escalation
  const escNote = document.getElementById("res-escalation");
  if (a.dispute_path?.escalation) {
    escNote.textContent = "↑ If that fails: " + a.dispute_path.escalation;
    escNote.classList.add("visible");
  } else {
    escNote.classList.remove("visible");
  }

  // Questions / missing evidence
  const cardQ = document.getElementById("card-questions");
  const allQ  = [...(a.questions || []), ...(a.missing_evidence || [])];
  if (allQ.length > 0 && !a.ready_to_submit) {
    cardQ.classList.remove("hidden");
    const qList = document.getElementById("res-questions-list");
    qList.innerHTML = "";
    allQ.forEach(q => {
      const div = document.createElement("div");
      div.className = "question-item";
      div.textContent = q;
      qList.appendChild(div);
    });
  } else {
    cardQ.classList.add("hidden");
  }

  // Draft letter
  document.getElementById("res-letter").textContent = a.draft_letter || "(No letter generated yet)";

  // Approval method
  const sub = a.submit_to;
  let approvalDesc = "Review the claim above, then approve to generate your submission package.";
  if (sub) {
    const methodMap = { email: "via email", web_form: "via web form", mail: "via mail", phone: "by phone" };
    approvalDesc = `Will submit to ${sub.name || "the company"} ${methodMap[sub.method] || ""}`;
    if (sub.form_url) approvalDesc += ` — ${sub.form_url}`;
  }
  document.getElementById("approval-method-desc").textContent = approvalDesc;

  resultsPanel.scrollIntoView({ behavior: "smooth" });
}

/* ── Loading helpers ── */
function showLoading(msg) {
  document.getElementById("start").classList.add("hidden");
  uploadSection.classList.add("hidden");
  resultsPanel.classList.add("hidden");
  loadingState.classList.remove("hidden");
  document.querySelector(".loading-text").textContent = msg || "Analyzing...";
}

function hideLoading() {
  loadingState.classList.add("hidden");
}

/* ── Reset ── */
window.resetApp = function () {
  state.files = [];
  state.analysis = null;
  state.claimPackage = null;
  state.pendingCompany = "";
  renderFileList();
  pastedText.value = "";
  userContext.value = "";
  document.getElementById("hero-input").value = "";
  uploadSection.classList.remove("hidden");
  document.getElementById("start").classList.remove("hidden");
  loadingState.classList.add("hidden");
  resultsPanel.classList.add("hidden");
  document.getElementById("autofill-panel").classList.add("hidden");
  document.getElementById("escalate-panel").classList.add("hidden");
  document.getElementById("confirmation-panel").classList.add("hidden");
  document.getElementById("confirm-reference-wrap").classList.add("hidden");
  window.scrollTo({ top: 0, behavior: "smooth" });
};
