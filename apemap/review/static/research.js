"use strict";
const initResearchPanel = (panel) => {
const statusNode = panel?.querySelector("[data-research-status]");
const results = panel?.querySelector("[data-research-results]");
const editor = panel?.querySelector("[data-evidence-editor]");
const feedback = panel?.querySelector("[data-evidence-feedback]");
const csrf = panel?.querySelector('input[name="csrf_token"]')?.value || "";
let workspaceDraft = panel?.querySelector('input[name="workspace_draft_token"]')?.value || "";

const safeURL = (value) => {
  try { const url = new URL(value, window.location.href); return ["http:", "https:"].includes(url.protocol) ? url.href : ""; }
  catch (_) { return ""; }
};
const node = (tag, text, className) => {
  const item = document.createElement(tag);
  if (text !== undefined && text !== null) item.textContent = String(text);
  if (className) item.className = className;
  return item;
};

document.documentElement?.classList.add("js-enabled");
const postJSON = async (url, form) => {
  const response = await fetch(url, { method: "POST", body: new FormData(form), credentials: "same-origin", headers: { Accept: "application/json" } });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "The request could not be completed.");
  return data;
};
const showError = (error) => { if (feedback) feedback.textContent = error.message || "The request could not be completed."; };

const renderSuggestions = (result, jobId) => {
  results.replaceChildren();
  if (result.status === "failed") { results.append(node("p", result.error, "error")); return; }
  const suggestions = Array.isArray(result.suggestions) ? result.suggestions : [];
  if (!suggestions.length) { results.append(node("p", "No cited suggestions were returned.")); return; }
  suggestions.forEach((suggestion, index) => {
    const article = node("article", undefined, "candidate-card");
    article.dataset.suggestion = "";
    article.append(node("h3", suggestion.source_title || "Cited source"));
    const sourceURL = safeURL(suggestion.source_url);
    const sourceLine = node("p");
    if (sourceURL) { const anchor = node("a", "Inspect source"); anchor.href = sourceURL; anchor.target = "_blank"; anchor.rel = "noopener noreferrer"; sourceLine.append(anchor); }
    sourceLine.append(document.createTextNode(` · Retrieved ${suggestion.retrieved_at || "date unknown"}`));
    article.append(sourceLine);
    article.append(node("p", `${suggestion.stance || "contextual"} · ${suggestion.claim_type || "Claim"}${suggestion.candidate_institution_ref ? ` · ${suggestion.candidate_institution_ref}` : ""}`));
    article.append(node("p", suggestion.excerpt_or_note || ""));
    const details = node("details", undefined, "technical-details"); details.append(node("summary", "Suggested claim and provenance")); details.append(node("pre", JSON.stringify(suggestion.claim_value, null, 2))); article.append(details);
    const form = node("form"); form.method = "post"; form.action = `/items/${encodeURIComponent(panel.dataset.reviewId)}/research/${encodeURIComponent(jobId)}/suggestions/${index}`; form.dataset.researchInspect = "";
    const csrfInput = node("input"); csrfInput.type = "hidden"; csrfInput.name = "csrf_token"; csrfInput.value = csrf;
    const label = node("label", undefined, "choice"); const check = node("input"); check.type = "checkbox"; check.name = "source_inspected"; check.value = "yes"; check.required = true; label.append(check, document.createTextNode("I inspected this source"));
    form.append(csrfInput, label, node("button", "Review evidence draft")); article.append(form);
    const dismiss = node("button", "Dismiss suggestion"); dismiss.type = "button"; dismiss.dataset.dismissSuggestion = ""; article.append(dismiss);
    results.append(article);
  });
};

const renderEditor = (data, inspectUrl) => {
  const value = data.evidence_values || {};
  editor.hidden = false; editor.replaceChildren();
  const heading = node("h3", "Review cited evidence"); editor.append(heading);
  const form = node("form"); form.method = "post"; form.action = `/items/${encodeURIComponent(panel.dataset.reviewId)}/evidence/preview`; form.dataset.evidencePreview = "";
  const add = (name, value, labelText, type = "text") => {
    const label = node("label", labelText); let input;
    if (type === "textarea") { input = node("textarea"); input.rows = name.includes("excerpt") ? 3 : 2; input.value = value ?? ""; }
    else { input = node("input"); input.type = type; input.value = value ?? ""; }
    input.name = name; label.append(input); form.append(label);
  };
  const token = node("input"); token.type = "hidden"; token.name = "csrf_token"; token.value = csrf; form.append(token);
  if (workspaceDraft) { const draft = node("input"); draft.type = "hidden"; draft.name = "workspace_draft_token"; draft.value = workspaceDraft; form.append(draft); }
  const valueClaim = value.evidence_claim_value;
  add("evidence_source_title", value.evidence_source_title || "", "Source title");
  add("evidence_source_type", value.evidence_source_type || "assisted-search", "Source type");
  add("evidence_source_url", value.evidence_source_url || "", "Source URL", "url");
  add("evidence_retrieved_at", value.evidence_retrieved_at || "", "Retrieved at");
  add("evidence_claim_type", value.evidence_claim_type || "identity", "Claim type");
  add("evidence_stance", value.evidence_stance || "contextual", "Stance");
  add("evidence_claim_value", typeof valueClaim === "string" ? valueClaim : "", "Claim value", "textarea");
  add("evidence_claim_value_json", value.evidence_claim_value_json || (valueClaim && typeof valueClaim === "object" ? JSON.stringify(valueClaim) : ""), "Structured claim value (JSON)", "textarea");
  add("evidence_excerpt_or_note", value.evidence_excerpt_or_note || "", "Excerpt or note", "textarea");
  if (value.evidence_candidate_institution_ref) add("evidence_candidate_institution_ref", value.evidence_candidate_institution_ref, "Candidate institution reference");
  const inspected = node("input"); inspected.type = "hidden"; inspected.name = "source_inspected"; inspected.value = "yes"; form.append(inspected);
  if (value.research_job_id) { for (const key of ["research_job_id", "research_suggestion_index"]) { const hidden = node("input"); hidden.type = "hidden"; hidden.name = key; hidden.value = value[key] || ""; form.append(hidden); } }
  form.append(node("button", "Validate evidence before retaining")); editor.append(form);
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); feedback.textContent = "Validating evidence…";
    try {
      const preview = await postJSON(form.action, form);
      editor.replaceChildren(node("h3", "Validated evidence preview"), node("p", "This is a cited source record only. It does not make a member or school decision."));
      const record = preview.record || {};
      editor.append(node("p", `${record.source_title || "Cited source"} · ${record.stance || "Contextual"} ${String(record.claim_type || "claim").replaceAll("_", " ")}${record.excerpt_or_note ? `: ${record.excerpt_or_note}` : ""}`));
      const source = safeURL(record.source_url); if (source) { const link = node("a", "Inspect cited source"); link.href = source; link.rel = "noopener noreferrer"; editor.append(link); }
      const provenance = node("details", undefined, "technical-details"); provenance.append(node("summary", "Full retention record"), node("pre", JSON.stringify(record, null, 2))); editor.append(provenance);
      const retain = node("button", "Retain this evidence"); retain.type = "button"; editor.append(retain);
      retain.addEventListener("click", async () => {
        feedback.textContent = "Retaining evidence…";
        try {
          const saveForm = document.createElement("form");
          const csrfField = node("input"); csrfField.name = "csrf_token"; csrfField.value = csrf;
          const previewField = node("input"); previewField.name = "evidence_preview_token"; previewField.value = preview.evidence_preview_token;
          const draftField = node("input"); draftField.name = "workspace_draft_token"; draftField.value = workspaceDraft;
          saveForm.append(csrfField, previewField, draftField);
          const saved = await postJSON(`/items/${encodeURIComponent(panel.dataset.reviewId)}/evidence/save`, saveForm);
          feedback.textContent = `Retained source ${saved.record.evidence_id}. It has not changed a decision.`;
          editor.replaceChildren(node("p", feedback.textContent));
          const claim = saved.record.claim_value;
          if (panel.dataset.reviewId.startsWith("member:")) {
            let retained = panel.querySelector("[data-member-retained]");
            if (!retained) {
              retained = node("details"); retained.dataset.memberRetained = "";
              retained.append(node("summary", "Retained evidence for this field"));
              panel.querySelector("[data-evidence-feedback]").after(retained);
            }
            const article = node("article", undefined, "retained-record");
            article.append(node("p", `${saved.record.stance} · ${saved.record.claim_type} · ${saved.record.source_title}`));
            const retainedURL = safeURL(saved.record.source_url);
            if (retainedURL) { const link = node("a", "Inspect retained source"); link.href = retainedURL; link.target = "_blank"; link.rel = "noopener noreferrer"; article.append(link); }
            const field = panel.dataset.reviewId.split(":").at(-1);
            const label = node("label", undefined, "choice"); const reference = node("input"); reference.type = "checkbox"; reference.name = `evidence_refs_${field}`; reference.value = saved.record.evidence_id; reference.setAttribute("form", "member-batch-form");
            label.append(reference, document.createTextNode("Reference this retained source in the member decision draft")); article.append(label);
            retained.append(article);
          } else {
            let retained = panel.querySelector("[data-school-retained]");
            if (!retained) {
              retained = node("details", undefined, "retained-records"); retained.dataset.schoolRetained = "";
              retained.append(node("summary", "Retained evidence"));
              panel.querySelector("[data-evidence-feedback]").after(retained);
            }
            const article = node("article", undefined, "retained-record");
            article.append(node("p", `${saved.record.stance} · ${saved.record.claim_type} · ${saved.record.source_title}`));
            const retainedURL = safeURL(saved.record.source_url);
            if (retainedURL) { const link = node("a", "Inspect retained source"); link.href = retainedURL; link.target = "_blank"; link.rel = "noopener noreferrer"; article.append(link); }
            const decisionForm = document.querySelector("#school-draft, #review-decision");
            if (decisionForm) {
              const label = node("label", undefined, "choice"); const reference = node("input"); reference.type = "checkbox"; reference.name = "evidence_refs"; reference.value = saved.record.evidence_id; reference.setAttribute("form", decisionForm.id);
              label.append(reference, document.createTextNode("Reference this source in the review decision")); article.append(label);
            }
            retained.append(article);
          }
          if (panel.dataset.reviewId.startsWith("member:") && saved.record.stance === "supports" && saved.record.claim_type === "member_value" && claim?.field === panel.dataset.reviewId.split(":").at(-1)) {
            const copyButton = node("button", "Copy retained value to member draft"); copyButton.type = "button"; copyButton.dataset.copyEvidence = saved.record.evidence_id; editor.append(copyButton);
          }
          if (!panel.dataset.reviewId.startsWith("member:") && saved.record.stance === "supports" && saved.record.claim_type === "identity" && saved.record.candidate_institution_ref) {
            const copyButton = node("button", "Copy retained institution to draft"); copyButton.type = "button"; copyButton.dataset.copyEvidence = saved.record.evidence_id; editor.append(copyButton);
          }
        } catch (error) { showError(error); }
      });
      feedback.textContent = "Evidence checked. Retain it explicitly to add it to the local ledger.";
    } catch (error) { showError(error); }
  });
};

document.addEventListener("submit", async (event) => {
  const form = event.target;
  const submitter = event.submitter;
  const isPanelStart = form.matches("[data-research-start]") ||
    submitter?.dataset.researchReviewId === panel.dataset.reviewId;
  if (isPanelStart) {
    event.preventDefault(); feedback.textContent = "Starting cited research…";
    try {
      const formData = new FormData(form);
      const providerSelect = panel.querySelector("[data-research-provider]");
      if (providerSelect) formData.set("provider_id", providerSelect.value);
      if (submitter?.name) formData.append(submitter.name, submitter.value);
      const response = await fetch(submitter?.formAction || form.action, { method: "POST", body: formData, credentials: "same-origin", headers: { Accept: "application/json" } });
      const job = await response.json(); if (!response.ok) throw new Error(job.error || "Research could not start.");
      if (job.workspace_draft_token) workspaceDraft = job.workspace_draft_token;
      statusNode.textContent = "Searching cited public sources…";
      const poll = async () => {
        try {
          const response = await fetch(job.status_url, { credentials: "same-origin", headers: { Accept: "application/json" } });
          const current = await response.json(); if (!response.ok) throw new Error(current.error || "Research status is unavailable.");
          if (current.status === "running") { window.setTimeout(poll, 1500); return; }
          renderSuggestions(current, job.job_id); statusNode.textContent = current.status === "complete" ? "Research finished. Inspect each source before retaining evidence." : "Research finished without a result.";
        } catch (error) { showError(error); }
      };
      window.setTimeout(poll, 500);
    } catch (error) { showError(error); }
  } else if (panel.contains(form) && form.matches("[data-research-inspect]")) {
    event.preventDefault();
    if (!form.querySelector('[name="source_inspected"]')?.checked) { showError(new Error("Inspect the cited source before reviewing it.")); return; }
    try { const data = await postJSON(form.action, form); renderEditor(data, form.action); feedback.textContent = "Review the claim and provenance before previewing."; }
    catch (error) { showError(error); }
  }
});

panel?.addEventListener("click", async (event) => {
  const dismiss = event.target.closest("[data-dismiss-suggestion]");
  if (dismiss) { dismiss.closest("[data-suggestion]")?.remove(); return; }
  const copy = event.target.closest("[data-copy-evidence]");
  if (copy) {
    try {
      const body = new URLSearchParams({ csrf_token: csrf, workspace_draft_token: workspaceDraft });
      const response = await fetch(`/items/${encodeURIComponent(panel.dataset.reviewId)}/evidence/${encodeURIComponent(copy.dataset.copyEvidence)}/copy`, { method: "POST", body, credentials: "same-origin", headers: { Accept: "application/json" } });
      const data = await response.json(); if (!response.ok) throw new Error(data.error || "This value cannot be copied.");
      const eventName = data.field ? "apemap:member-evidence-copy" : "apemap:school-evidence-copy";
      panel.dispatchEvent(new CustomEvent(eventName, { bubbles: true, detail: data }));
      feedback.textContent = data.field ? "Retained value copied into the member field draft. Review it before saving." : "Retained institution copied into the review draft. Review it before saving.";
    } catch (error) { showError(error); }
  }
});
};

document.querySelectorAll("[data-research-panel]").forEach(initResearchPanel);

// Preserve the full-page research result route as the no-script fallback.
const status = document.getElementById("research-status");
if (status && !document.querySelector("[data-research-panel]")) {
  const poll = async () => {
    try { const response = await fetch(status.dataset.statusUrl, { credentials: "same-origin" }); const result = await response.json(); if (result.status !== "running") { window.location.reload(); return; } window.setTimeout(poll, 2000); }
    catch (_) { status.textContent = "Status unavailable. Refresh to check this research job."; }
  };
  window.setTimeout(poll, 2000);
}
