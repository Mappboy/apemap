"use strict";

(() => {
  const form = document.querySelector("[data-school-draft]");
  if (!form) return;
  const reference = form.querySelector('[name="field_institution_ref"]');
  const payload = form.querySelector('[name="payload"]');
  const source = form.elements.namedItem("source_url") || form.querySelector('[name="source_url"]');
  const selected = document.querySelector("[data-selected-school]");
  const lookup = form.querySelector("[data-school-lookup]");
  const query = lookup.querySelector("[data-school-query]");
  const results = lookup.querySelector("[data-school-results]");
  const status = lookup.querySelector("[data-school-search-status]");
  const save = document.querySelector("[data-save-school]");
  const previewStatus = document.querySelector("[data-preview-status]");
  let timer;
  let active;
  let generation = 0;
  let comparisonRequest;

  function invalidatePreview() {
    if (save) {
      save.disabled = true;
      previewStatus.textContent = "Your draft changed. Preview again before saving.";
    }
  }

  function choose(ref, name) {
    reference.value = ref;
    notifyDraftChange(reference);
    try {
      const value = JSON.parse(payload.value);
      if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error();
      value.institution_ref = ref;
      payload.value = JSON.stringify(value, null, 2);
      notifyDraftChange(payload);
      if (selected) selected.textContent = `Selected: ${name} · ${ref}`;
    } catch {
      if (selected) selected.textContent = `Selected: ${name} · ${ref}. The advanced JSON is invalid and was left unchanged; fix it before previewing.`;
    }
    invalidatePreview();
    refreshComparison(ref);
  }

  function notifyDraftChange(control) {
    if (!control || typeof control.dispatchEvent !== "function") return;
    control.dispatchEvent(new Event("input", {bubbles: true}));
    control.dispatchEvent(new Event("change", {bubbles: true}));
  }

  function refreshComparison(ref) {
    if (!form.dataset.comparisonUrl || !ref) return;
    if (comparisonRequest) comparisonRequest.abort();
    const controller = new AbortController();
    comparisonRequest = controller;
    const url = new URL(form.dataset.comparisonUrl, window.location.href);
    url.searchParams.set("candidate_ref", ref);
    fetch(url, {signal: controller.signal, credentials: "same-origin", headers: {Accept: "application/json"}})
      .then(async response => {
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || "School comparison could not be refreshed.");
        if (!Array.isArray(body.rows) || !Array.isArray(body.evidence)) throw new Error("Invalid school comparison response.");
        if (comparisonRequest !== controller) return;
        renderComparison(body.rows);
        renderEvidence(body.evidence, body.unknown_gaps || []);
      })
      .catch(error => {
        if (error.name !== "AbortError" && comparisonRequest === controller) {
          if (selected) selected.textContent += " Comparison refresh failed; the server will validate this selection on preview.";
        }
      });
  }

  document.addEventListener("apemap:school-evidence-copy", event => {
    const detail = event.detail || {};
    const ref = detail.institution_ref || detail.value || "";
    if (!/^(?:acara:[0-9]+|manual:[a-z0-9][a-z0-9-]*)$/.test(ref)) return;
    choose(ref, detail.school_name || ref);
    if (detail.source_url && /^https?:\/\//i.test(detail.source_url)) {
      source.value = detail.source_url;
      notifyDraftChange(source);
    }
    for (const checkbox of document.querySelectorAll('[name="evidence_refs"]')) {
      if (checkbox.form === form && checkbox.value === detail.evidence_id) {
        checkbox.checked = true;
        notifyDraftChange(checkbox);
      }
    }
  });

  function renderComparison(rows) {
    const primaryBody = document.querySelector("[data-primary-comparison] tbody");
    const additional = document.querySelector("[data-additional-comparison]");
    const additionalBody = additional?.querySelector("[data-comparison-table] tbody");
    const summary = document.querySelector("[data-additional-comparison-summary]");
    if (!primaryBody) return;
    const primaryFields = new Set(["name", "locality", "acara identifier"]);
    const primaryRows = rows.filter(row => primaryFields.has((row.field || "").toLowerCase()));
    const remaining = rows.filter(row => !primaryFields.has((row.field || "").toLowerCase()));
    renderComparisonRows(primaryBody, primaryRows);
    if (additional) {
      additional.hidden = remaining.length === 0;
      if (summary) {
        const unknown = remaining.filter(row => (row.comparison || "unknown") === "unknown").length;
        summary.textContent = `More comparison facts · ${remaining.length} more · ${unknown} unknown`;
      }
      if (additionalBody) renderComparisonRows(additionalBody, remaining);
    }
  }

  function renderComparisonRows(body, rows) {
    body.replaceChildren();
    for (const row of rows) {
      const tr = document.createElement("tr");
      tr.dataset.comparison = row.comparison || "unknown";
      for (const value of [row.field, row.source, row.reviewed, row.proposed]) {
        const cell = document.createElement(value === row.field ? "th" : "td");
        cell.textContent = value || "Unknown";
        tr.append(cell);
      }
      const result = document.createElement("td");
      const badge = document.createElement("span");
      badge.className = `comparison-badge comparison-${row.comparison || "unknown"}`;
      badge.textContent = (row.comparison || "unknown").replaceAll("_", " ");
      result.append(badge, document.createTextNode(` · Evidence: ${row.summary || "No comparison details"}`));
      tr.append(result);
      body.append(tr);
    }
  }

  function renderEvidence(records, gaps) {
    const root = document.querySelector("[data-evidence-summary]");
    if (!root) return;
    const wasOpen = root.open;
    const counts = Object.fromEntries(["supports", "contradicts", "contextual"].map(stance => [
      stance, records.filter(item => item.stance === stance).length,
    ]));
    const replacement = document.createElement("details");
    replacement.className = "workspace-evidence";
    replacement.dataset.evidenceSummary = "";
    replacement.open = wasOpen;
    const summaryTitle = document.createElement("summary");
    summaryTitle.textContent = `Evidence: ${counts.supports} supports · ${counts.contradicts} conflicts · ${counts.contextual} contextual${gaps.length ? ` · ${gaps.length} unknown gaps` : ""}`;
    replacement.append(summaryTitle);
    for (const [stance, label] of [["supports", "Supports"], ["contradicts", "Conflicts"], ["contextual", "Context"]]) {
      const matches = records.filter(item => item.stance === stance);
      if (!matches.length) continue;
      const group = document.createElement("div"); group.className = `evidence-group evidence-${stance}`;
      const title = document.createElement("h3"); title.textContent = `${label} ${matches.length}`; group.append(title);
      const list = document.createElement("ul");
      for (const item of matches) {
        const li = document.createElement("li"); li.dataset.evidenceStance = stance;
        const excerpt = item.summary || item.claim || item.claim_value || item.excerpt_or_note;
        if (excerpt) { const p = document.createElement("p"); p.textContent = excerpt; li.append(p); }
        if (item.source_title) { const strong = document.createElement("strong"); strong.textContent = item.source_title; li.append(strong); }
        if (/^https?:\/\//i.test(item.source_url || "")) { const link = document.createElement("a"); link.href = item.source_url; link.rel = "noopener noreferrer"; link.textContent = item.source_title || item.source_url; li.append(link); }
        if (item.retrieved_at) { const retrieved = document.createElement("small"); retrieved.textContent = ` · ${item.retrieved_at}`; li.append(retrieved); }
        const provenance = document.createElement("details"); provenance.className = "technical-details";
        const provenanceTitle = document.createElement("summary"); provenanceTitle.textContent = "Full evidence provenance";
        const pre = document.createElement("pre"); pre.textContent = JSON.stringify(item, null, 2);
        provenance.append(provenanceTitle, pre); li.append(provenance);
        list.append(li);
      }
      group.append(list);
      replacement.append(group);
    }
    const unknown = document.createElement("div"); unknown.className = "evidence-gaps";
    const unknownTitle = document.createElement("h3"); unknownTitle.textContent = `Unknown or incomplete evidence · ${gaps.length}`; unknown.append(unknownTitle);
    if (gaps.length) {
      const list = document.createElement("ul");
      for (const gap of gaps) { const li = document.createElement("li"); li.textContent = gap; list.append(li); }
      unknown.append(list);
    }
    replacement.append(unknown);
    root.replaceWith(replacement);
  }

  function wireButtons(container) {
    for (const button of container.querySelectorAll("[data-choose-school]")) {
      button.addEventListener("click", event => {
        event.preventDefault();
        choose(button.dataset.chooseSchool, button.dataset.schoolName);
      });
    }
  }

  function cancel() {
    clearTimeout(timer);
    if (active) active.abort();
    active = null;
    generation += 1;
  }

  function clear(message) {
    results.replaceChildren();
    status.textContent = message;
  }

  async function search() {
    cancel();
    const current = generation;
    const text = query.value.trim();
    if (!text) { clear("Enter a school name to search the local ACARA register."); return; }
    clear("Searching the local ACARA register…");
    const controller = new AbortController();
    active = controller;
    const timeout = setTimeout(() => controller.abort(), 60000);
    try {
      const url = new URL(lookup.dataset.lookupUrl, window.location.href);
      url.searchParams.set("q", text);
      const response = await fetch(url, {
        signal: controller.signal, credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      const body = await response.json();
      if (current !== generation) return;
      if (!response.ok) throw new Error(body.error || "Local school search failed.");
      if (!Array.isArray(body.results)) throw new Error("Invalid local register response.");
      const rows = body.results.slice(0, 20).filter(row => row &&
        /^acara:[0-9]+$/.test(row.institution_ref) && typeof row.school_name === "string");
      if (!rows.length) {
        clear("No matching school was found in the local ACARA register. Try another name or use a saved manual institution reference.");
        return;
      }
      for (const row of rows) {
        const li = document.createElement("li");
        const name = document.createElement("strong");
        name.textContent = row.school_name;
        const metadata = document.createElement("p");
        metadata.textContent = [row.institution_ref, row.suburb || "Locality unknown",
          row.state || "State unknown", row.sector || "Sector unknown",
          row.school_type || "Type unknown",
          `Snapshot: ${(row.institution_status || "unknown").replaceAll("_", " ")}`].join(" · ");
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = "Use this school";
        button.addEventListener("click", () => choose(row.institution_ref, row.school_name));
        li.append(name, metadata, button);
        results.append(li);
      }
      status.textContent = `Showing ${rows.length} local results. ${rows.length === 20 ? "Refine the name for more specific results." : "Choose a school, then supply relationship evidence."}`;
    } catch (error) {
      if (current === generation) clear(error.name === "AbortError"
        ? "Local school search timed out. Try again."
        : `${error.message} Your review draft has not changed.`);
    } finally {
      clearTimeout(timeout);
      if (current === generation) active = null;
    }
  }

  wireButtons(form);
  if (form.dataset.comparisonUrl) {
    for (const button of document.querySelectorAll("[data-use-candidate]")) {
      button.addEventListener("click", event => {
        event.preventDefault();
        choose(button.dataset.chooseSchool, button.dataset.schoolName);
      });
    }
  }
  for (const button of form.querySelectorAll("[data-use-source]")) {
    button.addEventListener("click", event => {
      event.preventDefault();
      source.value = button.dataset.useSource;
      notifyDraftChange(source);
      invalidatePreview();
    });
  }
  for (const button of form.querySelectorAll("[data-find-school]")) {
    button.addEventListener("click", event => {
      event.preventDefault();
      form.querySelector("[data-school-search]").open = true;
      query.value = button.dataset.findSchool;
      invalidatePreview();
      search();
    });
  }
  lookup.querySelector("[data-school-search-button]").addEventListener("click", event => {
    event.preventDefault();
    search();
  });
  query.addEventListener("input", () => {
    cancel();
    clear("Waiting to search the local ACARA register…");
    if (query.value.trim()) timer = setTimeout(search, 250);
  });
  query.addEventListener("keydown", event => {
    if (event.key === "Enter") { event.preventDefault(); search(); }
  });
  reference.addEventListener("input", () => {
    selected.textContent = reference.value.trim()
      ? `Selected reference: ${reference.value.trim()} · validate to resolve this institution`
      : "No school selected";
  });
  form.addEventListener("input", invalidatePreview);
  form.addEventListener("change", invalidatePreview);
  for (const control of Array.from(form.elements || [])) {
    control.addEventListener("input", invalidatePreview);
    control.addEventListener("change", invalidatePreview);
  }
  if (reference.value.trim()) refreshComparison(reference.value.trim());
  for (const checkbox of document.querySelectorAll('[name="evidence_refs"]')) {
    if (checkbox.form === form) checkbox.addEventListener("change", invalidatePreview);
  }
})();

(() => {
  const form = document.querySelector("[data-resolution-outcomes]");
  if (!form) return;
  const action = form.elements.namedItem("action");
  const replacement = form.elements.namedItem("replacement_action");
  const mode = form.elements.namedItem("payload_mode");
  const fields = Array.from(form.querySelectorAll("[data-resolution-field]"));
  if (form.dataset.resolutionOutcomes !== undefined) {
    fields.push(...document.querySelectorAll(`[form="${form.id}"][data-resolution-field]`));
  }

  function updateResolutionFields() {
    const outcome = action.value === "supersede" ? replacement.value : action.value;
    const unresolved = mode.value === "guided" &&
      ["research", "reject", "ambiguous_name", "no_suitable_candidate"].includes(outcome);
    for (const field of fields) {
      field.disabled = unresolved;
      if (unresolved) {
        if (field.type === "checkbox") field.checked = false;
        else field.value = "";
      }
    }
    if (unresolved) {
      const selected = document.querySelector("[data-selected-school]");
      if (selected) selected.textContent = "No school selected; resolve each member's assertion independently.";
    }
  }

  form.addEventListener("change", updateResolutionFields);
  action.addEventListener("change", updateResolutionFields);
  updateResolutionFields();
})();
