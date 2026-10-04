"use strict";

(() => {
  const resultLimit = 20;
  const debounceMs = 250;

  for (const widget of document.querySelectorAll("[data-institution-lookup]")) {
    const form = widget.closest("form");
    const queryInput = widget.querySelector("[data-lookup-query]");
    const searchButton = widget.querySelector("[data-lookup-search]");
    const status = widget.querySelector("[data-lookup-status]");
    const results = widget.querySelector("[data-lookup-results]");
    const referenceInput = form.querySelector('[name="field_institution_ref"]');
    const payloadInput = form.querySelector('textarea[name="payload"]');
    let debounceTimer = null;
    let activeRequest = null;
    let generation = 0;

    function clearResults(message) {
      results.replaceChildren();
      results.hidden = true;
      status.textContent = message;
    }

    function cancelPending() {
      clearTimeout(debounceTimer);
      debounceTimer = null;
      if (activeRequest) activeRequest.abort();
      activeRequest = null;
      generation += 1;
    }

    function chooseInstitution(institution) {
      referenceInput.value = institution.institution_ref;
      try {
        const payload = JSON.parse(payloadInput.value);
        if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
          throw new Error("The payload must be a JSON object");
        }
        payload.institution_ref = institution.institution_ref;
        payloadInput.value = JSON.stringify(payload, null, 2);
        status.textContent = `Filled ${institution.institution_ref} for ${institution.school_name}. Validate and preview again before saving this reference; any earlier preview still describes its original decision.`;
      } catch {
        status.textContent = `Filled the reference field with ${institution.institution_ref}. The complete JSON payload is invalid and was left unchanged; fix it before validating and previewing.`;
      }
    }

    function showResults(institutions) {
      results.replaceChildren();
      for (const institution of institutions) {
        const item = document.createElement("li");
        const name = document.createElement("strong");
        name.textContent = institution.school_name;
        const details = document.createElement("span");
        details.textContent = [
          institution.institution_ref,
          `State: ${institution.state || "unknown"}`,
          `Suburb: ${institution.suburb || "unknown"}`,
          `Type: ${institution.school_type || "unknown"}`,
          `Sector: ${institution.sector || "unknown"}`,
          `Snapshot status: ${(institution.institution_status || "unknown").replaceAll("_", " ")}`,
        ].join(" · ");
        const choose = document.createElement("button");
        choose.type = "button";
        choose.textContent = `Choose ${institution.institution_ref}`;
        choose.addEventListener("click", () => chooseInstitution(institution));
        item.append(name, details, choose);
        results.append(item);
      }
      results.hidden = false;
      status.textContent = `Showing ${institutions.length} local register result${institutions.length === 1 ? "" : "s"}. Choose a school to fill its reference${institutions.length === resultLimit ? "; refine the school name for more specific results" : ""}.`;
    }

    async function search() {
      cancelPending();
      const currentGeneration = generation;
      const query = queryInput.value.trim();
      if (!query) {
        clearResults("Enter a school name to search the local ACARA register.");
        return;
      }
      clearResults("Searching the local ACARA register…");
      const controller = new AbortController();
      activeRequest = controller;
      const timeout = setTimeout(() => controller.abort(), 60000);
      try {
        const url = new URL(widget.dataset.lookupUrl, window.location.href);
        url.searchParams.set("q", query);
        const response = await fetch(url, {
          signal: controller.signal,
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        });
        const body = await response.json();
        if (currentGeneration !== generation) return;
        if (!response.ok) throw new Error(body.error || "Local school search failed.");
        if (!Array.isArray(body.results)) throw new Error("Invalid local register response.");
        const institutions = body.results.slice(0, resultLimit).filter(
          (institution) => institution &&
            /^acara:[0-9]+$/.test(institution.institution_ref) &&
            typeof institution.school_name === "string",
        );
        if (!institutions.length) {
          clearResults("No matching school was found in the local ACARA register. Try another name, or enter a sourced institution reference yourself.");
          return;
        }
        showResults(institutions);
      } catch (error) {
        if (currentGeneration === generation) {
          clearResults(error.name === "AbortError"
            ? "Local school search timed out. Try again."
            : `${error.message} Your review draft has not changed.`);
        }
      } finally {
        clearTimeout(timeout);
        if (currentGeneration === generation) activeRequest = null;
      }
    }

    queryInput.addEventListener("input", () => {
      cancelPending();
      if (!queryInput.value.trim()) {
        clearResults("Enter a school name to search the local ACARA register.");
        return;
      }
      clearResults("Waiting to search the local ACARA register…");
      debounceTimer = setTimeout(search, debounceMs);
    });
    queryInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        search();
      }
    });
    searchButton.addEventListener("click", search);
  }
})();
