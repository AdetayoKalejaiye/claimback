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

    state.analysis = data.analysis;
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

/* ── Approve ── */
document.getElementById("btn-approve").addEventListener("click", async () => {
  const res = await fetch("/approve", { method: "POST" });
  const data = await res.json();

  if (data.error) { alert("Error: " + data.error); return; }

  state.claimPackage = data.claim_package;

  // Hide approval bar, show confirmation
  document.getElementById("approval-bar").classList.add("hidden");
  const confirm = document.getElementById("confirmation-panel");
  confirm.classList.remove("hidden");

  const sub = data.claim_package.submit_to;
  let instrText = "";
  if (sub) {
    const method = sub.method || "web_form";
    const name   = sub.name || "the company";
    if (method === "email") instrText = `Send the letter below to ${name} at ${sub.address || "their disputes email"}.`;
    else if (method === "web_form") instrText = `Submit via ${name}'s online portal${sub.form_url ? ": " + sub.form_url : ""}.`;
    else if (method === "mail") instrText = `Mail the letter to ${name} at ${sub.address}.`;
    else instrText = `Contact ${name} via ${method}.`;
  }

  document.getElementById("confirm-instructions").textContent = instrText;
});

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
  renderFileList();
  pastedText.value = "";
  userContext.value = "";
  uploadSection.classList.remove("hidden");
  loadingState.classList.add("hidden");
  resultsPanel.classList.add("hidden");
  window.scrollTo({ top: 0, behavior: "smooth" });
};
