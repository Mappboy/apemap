"use strict";
document.querySelectorAll("[data-research-review]").forEach((form) => {
  const action = form.querySelector('[name="action"]');
  const notes = form.querySelector('[name="notes"]');
  const dispositions = [...form.querySelectorAll("[data-evidence-stance]")];
  const identityError = form.querySelector('[name="identity_error"]');
  const warning = form.querySelector("[data-review-warning]");
  const record = form.querySelector("[data-record-review]");
  const update = () => {
    let message = "";
    if (!dispositions.length) {
      message = "Acceptance requires cited, inspected evidence. Choose Needs further research or Reject proposal and add notes.";
    } else if (identityError?.checked) {
      message = "This proposal identifies the wrong person. Choose Needs further research or Reject proposal and add notes; acceptance is blocked.";
    } else if (dispositions.some((select) => select.value === "unavailable")) {
      message = "An unavailable source blocks acceptance. Choose Needs further research or Reject proposal and explain the access gap in notes.";
    } else if (dispositions.some((select) => select.value !== select.dataset.evidenceStance)) {
      message = "A source disposition differs from the saved evidence conclusion. Acceptance is blocked. Choose Needs further research or Reject proposal and add notes; corrected conclusions require a new research batch.";
    }
    warning.textContent = message;
    warning.hidden = !message;
    record.disabled = action.value === "accept" && Boolean(message);
    notes.required = action.value !== "accept";
    return !record.disabled;
  };
  form.addEventListener("change", update);
  form.addEventListener("submit", (event) => {
    if (!update()) event.preventDefault();
  });
  update();
});
