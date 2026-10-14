"use strict";
(() => {
  const batch = document.querySelector("[data-research-batch]");
  if (!batch) return;
  const form = batch.querySelector("[data-research-review]");
  let dirty = false;
  const invalidatePreview = (message) => {
    batch.querySelectorAll("[data-apply-preview]").forEach((button) => { button.disabled = true; });
    const warning = batch.querySelector("[data-preview-warning]");
    if (warning) { warning.textContent = message; warning.hidden = false; }
  };
  const leave = (event) => {
    if (!dirty) return;
    if (!window.confirm("Discard unsaved review changes?")) event.preventDefault();
    else dirty = false;
  };
  batch.querySelectorAll("[data-batch-navigation]").forEach((link) => link.addEventListener("click", leave));
  batch.querySelectorAll("[data-batch-navigation-form], [data-batch-operation]").forEach((other) => other.addEventListener("submit", leave));
  window.addEventListener("beforeunload", (event) => {
    if (dirty) { event.preventDefault(); event.returnValue = ""; }
  });

  if (form) {
    const action = form.querySelector('[name="action"]');
    const notes = form.querySelector('[name="notes"]');
    const dispositions = [...form.querySelectorAll("[data-evidence-stance]")];
    const inspections = [...form.querySelectorAll("[data-source-inspected]")];
    const identityError = form.querySelector('[name="identity_error"]');
    const warning = form.querySelector("[data-review-warning]");
    const records = [...form.querySelectorAll("[data-record-review]")];
    const update = () => {
      const completed = inspections.filter((input) => input.checked).length;
      form.querySelector("[data-inspection-count]").textContent = `${completed} / ${inspections.length}`;
      let message = "";
      if (!dispositions.length && action.value === "accept") {
        message = "Acceptance requires cited, inspected evidence. Choose Needs further research or Reject proposal and add notes.";
      } else if (form.dataset.acceptReady === "false" && action.value === "accept") {
        message = "This proposal identity remains unresolved. Choose Needs further research or Reject proposal and add notes.";
      } else if (identityError?.checked && action.value === "accept") {
        message = "This proposal identifies the wrong person. Choose Needs further research or Reject proposal and add notes; acceptance is blocked.";
      } else if (dispositions.some((select) => select.value === "unavailable") && action.value === "accept") {
        message = "An unavailable source blocks acceptance. Choose Needs further research or Reject proposal and explain the access gap in notes.";
      } else if (dispositions.some((select) => select.value !== select.dataset.evidenceStance) && action.value === "accept") {
        message = "A source disposition differs from the saved evidence conclusion. Acceptance is blocked; corrected conclusions require a new research batch. Choose Needs further research or Reject proposal and add notes.";
      } else if (completed !== inspections.length) {
        message = "Complete inspection or record an access attempt and disposition for every attached source.";
      }
      warning.textContent = message;
      warning.hidden = !message;
      const blocked = Boolean(message) || form.dataset.reviewReady === "false";
      records.forEach((record) => { record.disabled = blocked; });
      notes.required = action.value !== "accept";
      return !blocked;
    };
    const change = () => {
      dirty = true;
      form.querySelector("[data-review-dirty]").hidden = false;
      invalidatePreview("Review inputs changed. Save the review and prepare a new exact preview before applying.");
      update();
    };
    form.addEventListener("input", change);
    form.addEventListener("change", change);
    form.addEventListener("submit", (event) => {
      if (!update()) event.preventDefault();
      else queueMicrotask(() => { if (!event.defaultPrevented) dirty = false; });
    });
    update();
  }

  const operations = batch.querySelector("#research-batch-operations");
  if (operations) {
    const updateScope = () => {
      const selected = operations.querySelector('[name="scope"]:checked');
      if (!selected) return;
      const count = Number(selected.dataset.scopeCount);
      const retained = selected.dataset.scopeRetained === "true";
      batch.querySelector("[data-selected-count]").textContent = String(count);
      batch.querySelector("[data-retention-status]").textContent = retained ? "Evidence retained." : "Evidence retention pending.";
      operations.querySelector("[data-stage-retain]").disabled = !count;
      operations.querySelector("[data-stage-preview]").disabled = !count || !retained;
      operations.querySelector("[data-stage-revalidate]").disabled = !count || !retained;
      batch.querySelectorAll('input[type="hidden"][name="scope"]').forEach((input) => { input.value = selected.value; });
    };
    operations.addEventListener("change", (event) => {
      if (event.target.name !== "scope") return;
      updateScope();
      invalidatePreview("Approval scope changed. Prepare a new exact preview before applying.");
    });
    updateScope();
  }
})();
