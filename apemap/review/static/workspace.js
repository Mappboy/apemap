(() => {
  const workspace = document.querySelector("[data-review-workspace]");
  if (!workspace) return;
  let dirty = false;
  const markDirty = () => {
    dirty = true;
    workspace.dataset.dirty = "true";
    document.querySelectorAll('[data-workspace-action="save"], [data-save-action]').forEach((el) => {
      el.dataset.invalidated = "true";
      el.setAttribute("aria-disabled", "true");
      if (el instanceof HTMLButtonElement || el instanceof HTMLInputElement) el.disabled = true;
    });
  };
  const isDecisionForm = (form) => form instanceof HTMLFormElement && (
    form.matches("form[data-decision-draft], #school-draft, #member-batch-form, [data-assertion-draft]")
  );
  const isDecisionControl = (control) => {
    if (!(control instanceof HTMLElement) || !isDecisionForm(control.form)) return false;
    return !control.name?.startsWith("research_provider_id") && control.name !== "provider_id";
  };
  document.addEventListener("input", (event) => {
    if (isDecisionControl(event.target)) markDirty();
  });
  document.addEventListener("change", (event) => {
    if (isDecisionControl(event.target)) markDirty();
  });
  document.addEventListener("apemap:school-evidence-copy", (event) => {
    const form = document.querySelector("#review-decision");
    if (!form) return;
    const detail = event.detail || {};
    const target = form.elements.namedItem("field_institution_ref");
    if (target && detail.institution_ref) {
      target.value = detail.institution_ref;
      target.dispatchEvent(new Event("input", { bubbles: true }));
      target.dispatchEvent(new Event("change", { bubbles: true }));
    }
    const source = form.elements.namedItem("source_url");
    if (source && detail.source_url) {
      source.value = detail.source_url;
      source.dispatchEvent(new Event("input", { bubbles: true }));
      source.dispatchEvent(new Event("change", { bubbles: true }));
    }
    const evidence = form.elements.namedItem("evidence_refs");
    if (evidence && detail.evidence_id) {
      const controls = evidence.length === undefined ? [evidence] : [...evidence];
      const selected = controls.find((control) => control.value === detail.evidence_id);
      if (selected) { selected.checked = true; selected.dispatchEvent(new Event("change", { bubbles: true })); }
    }
  });
  const assertionForm = document.querySelector("form[data-assertion-draft][data-comparison-url]");
  if (assertionForm) {
    let comparisonRequest;
    const refreshAssertionComparison = async () => {
      const ref = assertionForm.elements.namedItem("field_institution_ref")?.value || "";
      const url = new URL(assertionForm.dataset.comparisonUrl, window.location.href);
      if (ref) url.searchParams.set("candidate_ref", ref);
      comparisonRequest?.abort();
      comparisonRequest = new AbortController();
      const requestController = comparisonRequest;
      try {
        const response = await fetch(url, { headers: { Accept: "application/json" }, signal: requestController.signal });
        if (!response.ok) return;
        const data = await response.json();
        if (comparisonRequest !== requestController) return;
        const table = document.querySelector("[data-comparison-table] tbody");
        if (table) {
          table.replaceChildren(...(data.rows || []).map((row) => {
            const tr = document.createElement("tr");
            tr.dataset.comparison = row.comparison || "unknown";
            const field = document.createElement("th"); field.scope = "row"; field.textContent = row.field || "Unknown"; tr.append(field);
            [row.source, row.reviewed, row.proposed].forEach((value) => {
              const td = document.createElement("td"); td.textContent = value || "Unknown"; tr.append(td);
            });
            const result = document.createElement("td");
            const badge = document.createElement("span"); badge.className = `comparison-badge comparison-${row.comparison || "unknown"}`; badge.textContent = (row.comparison || "unknown").replace(/^./, (letter) => letter.toUpperCase());
            result.append(badge, document.createTextNode(` · Evidence: ${row.summary || "Unknown"}`)); tr.append(result);
            return tr;
          }));
        }
        const evidence = document.querySelector("[data-evidence-summary]");
        if (evidence) {
          const counts = { supports: 0, contradicts: 0, contextual: 0 };
          (data.evidence || []).forEach((record) => { if (counts[record.stance] !== undefined) counts[record.stance]++; });
          const summary = evidence.querySelector("summary");
          if (summary) summary.textContent = `Evidence: ${counts.supports} supports · ${counts.contradicts} conflicts · ${counts.contextual} contextual${(data.unknown_gaps || []).length ? ` · ${data.unknown_gaps.length} unknown gaps` : ""}`;
          const gaps = evidence.querySelector(".evidence-gaps");
          if (gaps) { gaps.replaceChildren(); const heading = document.createElement("h3"); heading.textContent = "Unknown or incomplete evidence"; gaps.append(heading); const list = document.createElement("ul"); (data.unknown_gaps || []).forEach((gap) => { const li = document.createElement("li"); li.textContent = gap; list.append(li); }); gaps.append(list); }
        }
        const selected = document.querySelector("[data-selected-assertion]");
        if (selected) selected.textContent = ref ? `Selected candidate: ${ref}` : "No candidate selected.";
      } catch (error) { if (error.name !== "AbortError") console.error("Could not refresh assertion comparison", error); }
    };
    assertionForm.addEventListener("input", (event) => { if (event.target.name === "field_institution_ref") refreshAssertionComparison(); });
    assertionForm.addEventListener("change", (event) => { if (event.target.name === "field_institution_ref") refreshAssertionComparison(); });
    document.addEventListener("click", (event) => {
      const button = event.target instanceof Element ? event.target.closest("[data-assertion-candidate]") : null;
      if (!button) return;
      const input = assertionForm.elements.namedItem("field_institution_ref");
      if (!input) return;
      event.preventDefault(); input.value = button.value;
      input.dispatchEvent(new Event("input", { bubbles: true })); input.dispatchEvent(new Event("change", { bubbles: true }));
    });
  }
  document.querySelectorAll("form[data-decision-draft], #school-draft, #member-batch-form, [data-assertion-draft]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      const submitter = event.submitter;
      if (submitter?.matches?.("[data-research-start], [data-institution-generate]") || submitter?.dataset?.researchReviewId) return;
      queueMicrotask(() => {
      if (!event.defaultPrevented) {
        dirty = false;
        workspace.dataset.dirty = "false";
      }
      });
    });
  });
  window.addEventListener("apemap:institution-navigation", () => { dirty = false; });
  window.addEventListener("beforeunload", (event) => {
    if (dirty) { event.preventDefault(); event.returnValue = ""; }
  });

  const editable = (target) => target instanceof Element && (
    target.matches("input, textarea, select, button") ||
    target.isContentEditable === true ||
    Boolean(target.closest("[contenteditable]:not([contenteditable='false'])"))
  );
  document.addEventListener("keydown", (event) => {
    if (event.isComposing || event.altKey !== true || event.shiftKey !== true || event.ctrlKey || event.metaKey || editable(event.target)) return;
    const action = { p: "previous", n: "next", v: "preview", r: "research" }[event.key.toLowerCase()];
    if (!action) return;
    if (action === "research") {
      const disclosures = [...document.querySelectorAll("details[data-research-disclosure]")];
      const details = disclosures.find((item) => !item.open) || disclosures[0];
      if (details) {
        event.preventDefault();
        details.open = true;
        details.scrollIntoView({ block: "nearest" });
        details.querySelector("[data-research-open]")?.focus();
      }
      return;
    }
    const control = document.querySelector(`[data-workspace-action="${action}"]`);
    if (!control || control.hasAttribute("disabled") || control.dataset.invalidated === "true") return;
    event.preventDefault();
    if (control instanceof HTMLAnchorElement) window.location.assign(control.href);
    else control.click();
  });
})();
