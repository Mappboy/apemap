"use strict";
const memberForm = document.getElementById("member-batch-form");
if (memberForm) {
  const saves = ["save-batch-btn", "save-next-btn"]
    .map((id) => document.getElementById(id))
    .filter(Boolean);
  const message = document.getElementById("save-disabled-msg");
  const disableMemberSave = () => {
    saves.forEach((button) => { button.disabled = true; });
    if (message) message.hidden = false;
  };
  memberForm.addEventListener("input", disableMemberSave);
  memberForm.addEventListener("change", disableMemberSave);
  document.addEventListener("apemap:member-evidence-copy", (event) => {
    const { value, field, evidence_id: evidenceId, source_url: sourceUrl } = event.detail || {};
    if (!field || !["gender", "date_of_birth", "wikidata_id"].includes(field)) return;
    const elements = [...memberForm.elements];
    const valueInput = elements.find((item) => item.name === `value_${field}`);
    const sourceInput = elements.find((item) => item.name === `source_url_${field}`);
    const evidenceInput = elements.find((item) => item.name === `evidence_refs_${field}` && item.value === evidenceId);
    if (valueInput) { valueInput.value = value ?? ""; valueInput.dispatchEvent(new Event("input", { bubbles: true })); }
    if (sourceInput) { sourceInput.value = sourceUrl ?? ""; sourceInput.dispatchEvent(new Event("input", { bubbles: true })); }
    if (evidenceInput) { evidenceInput.checked = true; evidenceInput.dispatchEvent(new Event("change", { bubbles: true })); }
  });
}
