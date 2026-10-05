"use strict";

(() => {
  const form = document.querySelector("[data-school-draft]");
  if (!form) return;
  const reference = form.querySelector('[name="field_institution_ref"]');
  const payload = form.querySelector('[name="payload"]');
  const source = form.querySelector('[name="source_url"]');
  const selected = form.querySelector("[data-selected-school]");
  const lookup = form.querySelector("[data-school-lookup]");
  const query = lookup.querySelector("[data-school-query]");
  const results = lookup.querySelector("[data-school-results]");
  const status = lookup.querySelector("[data-school-search-status]");
  const save = document.querySelector("[data-save-school]");
  const previewStatus = document.querySelector("[data-preview-status]");
  let timer;
  let active;
  let generation = 0;

  function invalidatePreview() {
    if (save) {
      save.disabled = true;
      previewStatus.textContent = "Your draft changed. Preview again before saving.";
    }
  }

  function choose(ref, name) {
    reference.value = ref;
    try {
      const value = JSON.parse(payload.value);
      if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error();
      value.institution_ref = ref;
      payload.value = JSON.stringify(value, null, 2);
      selected.textContent = `Selected: ${name} · ${ref}`;
    } catch {
      selected.textContent = `Selected: ${name} · ${ref}. The advanced JSON is invalid and was left unchanged; fix it before previewing.`;
    }
    invalidatePreview();
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
  for (const button of form.querySelectorAll("[data-use-source]")) {
    button.addEventListener("click", event => {
      event.preventDefault();
      source.value = button.dataset.useSource;
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
})();
