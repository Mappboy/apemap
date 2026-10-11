"""Execute school chooser interactions offline, including preview invalidation."""

from pathlib import Path
import shutil
import subprocess

import pytest

from tests.test_review_lookup_browser import BROWSER_HARNESS

SCRIPT = Path(__file__).resolve().parents[1] / "apemap/review/static/school-mapping.js"

# Reuse the existing minimal DOM, deterministic clock and local-only fetch guard.
HARNESS = (
    BROWSER_HARNESS.split("const form =")[0]
    + r"""
Element.prototype.dispatchEvent = function(event) {
  this.emit(event.type);
  if (event.bubbles && this.parentElement) this.parentElement.emit(event.type);
  return true;
};
Element.prototype.querySelectorAll = function(selector) {
  return this.queries.get(selector) || [];
};
const form = new Element("form");
form.id = "school-draft";
let submissions = 0;
form.submit = form.requestSubmit = () => { submissions++; };
const ref = new Element("input");
ref.value = ["external_candidate", "comparison_evidence"].includes(scenario) ? "" : "acara:99";
const payload = new Element("textarea");
payload.value = JSON.stringify({recorded_name: "Original School", institution_ref: "acara:99", extra: {retained: true}});
const source = new Element("input"); source.value = "https://example.org/original"; source.form = form;
const selected = new Element("p");
const lookup = new Element("div"); lookup.dataset.lookupUrl = "/institutions/lookup";
const query = new Element("input");
const results = new Element("ul");
const status = new Element("p");
const searchButton = new Element("button");
const searchDetails = new Element("details");
const save = new Element("button");
const previewStatus = new Element("p");
const evidence = new Element("input"); evidence.form = form; evidence.value = "evidence-1";
const unrelatedEvidence = new Element("input"); unrelatedEvidence.form = null;
const choose = new Element("button");
choose.dataset.chooseSchool = "acara:123"; choose.dataset.schoolName = "Selected School";
const useSource = new Element("button"); useSource.dataset.useSource = "https://example.org/chosen";
const find = new Element("button"); find.dataset.findSchool = "School lead";
const action = new Element("select"); action.value = "map"; action.form = form;
const replacement = new Element("select"); replacement.value = "map";
const mode = new Element("select"); mode.value = "guided";
const relationship = new Element("select"); relationship.value = "direct";
const candidate = new Element("button"); candidate.type = "submit";
candidate.dataset.chooseSchool = "acara:321"; candidate.dataset.schoolName = "Candidate School";
candidate.closest = () => form;
const workspace = new Element("section"); workspace.dataset.dirty = "false";
const evidenceSummary = new Element("details"); evidenceSummary.open = true;
const comparisonBody = new Element("tbody");
const additionalComparisonBody = new Element("tbody");
const additionalComparison = new Element("details"); additionalComparison.hidden = false;
const additionalComparisonSummary = new Element("summary");
additionalComparison.querySelector = selector => selector === '[data-comparison-table] tbody' ? additionalComparisonBody : null;
form.elements = {namedItem(name) { return ({source_url: source, action, replacement_action: replacement, payload_mode: mode})[name] || null; }};
source.addEventListener("input", () => { workspace.dataset.dirty = "true"; });
source.addEventListener("change", () => { workspace.dataset.dirty = "true"; });
form.dataset.resolutionOutcomes = "";
if (["external_candidate", "comparison_evidence"].includes(scenario)) form.dataset.comparisonUrl = "/comparison";
form.addEventListener("input", () => { workspace.dataset.dirty = "true"; });
form.append(ref, payload, lookup, searchDetails, choose, useSource, find);
lookup.append(query, results, status, searchButton);
for (const [selector, element] of [
  ['[name="field_institution_ref"]', ref], ['[name="payload"]', payload],
  ['[data-selected-school]', selected],
  ['[data-school-lookup]', lookup], ['[data-school-search]', searchDetails],
  ['[data-choose-school]', [choose]], ['[data-use-source]', [useSource]],
  ['[data-find-school]', [find]],
]) form.queries.set(selector, element);
for (const [selector, element] of [
  ['[data-school-query]', query], ['[data-school-results]', results],
  ['[data-school-search-status]', status], ['[data-school-search-button]', searchButton],
]) lookup.queries.set(selector, element);
const document = {
  listeners: new Map(),
  addEventListener(type, listener) { this.listeners.set(type, listener); },
  emit(type, detail) { this.listeners.get(type)?.({type, detail}); },
  querySelectorAll(selector) {
    if (selector === '[name="evidence_refs"]') return [evidence, unrelatedEvidence];
    if (selector === "[data-use-candidate]") return [candidate];
    if (selector === '[form="school-draft"][data-resolution-field]') return [relationship];
    assert.fail(`Unexpected selector: ${selector}`);
  },
  querySelector(selector) {
    return new Map([['[data-school-draft]', form], ['[data-save-school]', save],
      ['[data-preview-status]', previewStatus], ['[data-selected-school]', selected],
      ['[data-resolution-outcomes]', form], ['[data-review-workspace]', workspace],
      ['[data-evidence-summary]', evidenceSummary], ['[data-primary-comparison] tbody', comparisonBody],
      ['[data-additional-comparison]', additionalComparison],
      ['[data-additional-comparison-summary]', additionalComparisonSummary]]).get(selector);
  },
  createElement(tagName) { return new Element(tagName); },
  createTextNode(value) { const node = new Element("text"); node.textContent = value; return node; },
};
evidenceSummary.replaceWith = replacement => { evidenceSummary.replacement = replacement; };
"""
    + "let now ="
    + BROWSER_HARNESS.split("let now =")[1].split("vm.runInNewContext")[0]
    + r"""
vm.runInNewContext(fs.readFileSync(scriptPath, "utf8"), {
  document, window: {location: {href: "http://127.0.0.1:8765/items/school:fixture"}},
  URL, AbortController, fetch: fetchLocal, setTimeout: setTimer,
  clearTimeout: id => timers.delete(id), Event: class { constructor(type, options) { this.type = type; this.bubbles = options.bubbles; } },
}, {filename: scriptPath});
function buttons(node) { return node.children.flatMap(child => [
  ...(child.tagName === "button" ? [child] : []), ...buttons(child)]); }
async function answer(index, rows) {
  requests[index].resolve({ok: true, json: async () => ({results: rows})});
  await new Promise(resolve => setImmediate(resolve));
}
async function answerComparison(index) {
  const fields = ["name", "locality", "ACARA identifier", "state", "My School identifier", "sector", "type", "coordinates", "historical names"];
  requests[index].resolve({ok: true, json: async () => ({
    rows: fields.map((field, index) => ({field, source: "Old School", proposed: "Candidate School", comparison: index < 3 ? "different" : index < 7 ? "unknown" : "matches", summary: "Review identity evidence"})),
    evidence: [{stance: "contradicts", summary: "Different locality", source_title: "School history", source_url: "https://example.org/history", retrieved_at: "2026-01-01"}],
    unknown_gaps: ["Attendance-era locality is unknown"],
  })});
  await new Promise(resolve => setImmediate(resolve));
}
const rows = [{institution_ref: "acara:456", school_name: "School <img src=x>", state: "TAS", suburb: "Hobart"}];
async function main() {
  if (scenario === "selection") {
    choose.emit("click");
    assert.equal(ref.value, "acara:123");
    assert.equal(source.value, "https://example.org/original");
    assert.deepEqual(JSON.parse(payload.value).extra, {retained: true});
    assert.equal(save.disabled, true);
    assert.ok(previewStatus.textContent.includes("Preview again"));
    useSource.emit("click");
    assert.equal(source.value, "https://example.org/chosen");
    assert.equal(ref.value, "acara:123");
  } else if (scenario === "edits") {
    form.emit("input"); assert.equal(save.disabled, true);
    save.disabled = false;
    form.emit("change"); assert.equal(save.disabled, true);
    payload.value = "{unfinished";
    choose.emit("click");
    assert.equal(payload.value, "{unfinished");
    assert.ok(selected.textContent.includes("invalid"));
  } else if (scenario === "evidence_selection") {
    assert.equal(save.disabled, undefined);
    unrelatedEvidence.emit("change"); assert.equal(save.disabled, undefined);
    evidence.emit("change"); assert.equal(save.disabled, true);
    assert.ok(previewStatus.textContent.includes("Preview again"));
  } else if (scenario === "search") {
    find.emit("click");
    assert.equal(searchDetails.open, true);
    assert.equal(requests[0].url.searchParams.get("q"), "School lead");
    const originalRef = ref.value;
    await answer(0, rows);
    assert.equal(ref.value, originalRef);
    assert.ok(results.textContent.includes("School <img src=x>"));
    buttons(results)[0].emit("click");
    assert.equal(ref.value, "acara:456");
    assert.equal(source.value, "https://example.org/original");
    assert.equal(save.disabled, true);
  } else if (scenario === "debounce_stale") {
    query.value = "First"; query.emit("input");
    advance(249); assert.equal(requests.length, 0);
    advance(1); assert.equal(requests.length, 1);
    query.value = "Second"; query.emit("keydown", {key: "Enter"});
    assert.equal(requests[0].options.signal.aborted, true);
    await answer(1, rows);
    await answer(0, [{...rows[0], school_name: "Stale school"}]);
    assert.equal(results.textContent.includes("Stale school"), false);
    assert.ok(results.textContent.includes("School <img src=x>"));
  } else if (scenario === "empty_timeout") {
    query.value = ""; searchButton.emit("click");
    assert.equal(requests.length, 0);
    query.value = "School"; searchButton.emit("click");
    advance(60000);
    assert.equal(requests[0].options.signal.aborted, true);
    const error = new Error(); error.name = "AbortError";
    requests[0].reject(error); await new Promise(resolve => setImmediate(resolve));
    assert.ok(status.textContent.includes("timed out"));
    assert.equal(ref.value, "acara:99");
  } else if (scenario === "failed_no_results") {
    query.value = "School"; searchButton.emit("click");
    await answer(0, []);
    assert.ok(status.textContent.includes("No matching school"));
    searchButton.emit("click");
    requests[1].resolve({ok:false, json:async () => ({error:"Register unavailable"})});
    await new Promise(resolve => setImmediate(resolve));
    assert.ok(status.textContent.includes("Register unavailable"));
    assert.equal(ref.value, "acara:99");
    assert.equal(source.value, "https://example.org/original");
  } else if (scenario === "external_controls") {
    choose.emit("click");
    assert.equal(ref.value, "acara:123");
    assert.equal(workspace.dataset.dirty, "true");
    useSource.emit("click");
    assert.equal(source.value, "https://example.org/chosen");
    assert.equal(workspace.dataset.dirty, "true");
  } else if (scenario === "associated_outcome") {
    action.value = "ambiguous_name";
    action.emit("change");
    assert.equal(relationship.disabled, true);
    assert.equal(relationship.value, "");
  } else if (scenario === "external_candidate") {
    candidate.emit("click");
    assert.equal(ref.value, "acara:321");
    assert.equal(workspace.dataset.dirty, "true");
    assert.equal(submissions, 0);
  } else if (scenario === "fallback_candidate") {
    candidate.emit("click");
    assert.equal(submissions, 1);
  } else if (scenario === "comparison_evidence") {
    candidate.emit("click");
    await answerComparison(0);
    assert.equal(evidenceSummary.replacement.tagName, "details");
    assert.equal(evidenceSummary.replacement.open, true);
    assert.ok(evidenceSummary.replacement.textContent.includes("0 supports · 1 conflicts · 0 contextual · 1 unknown gaps"));
    assert.ok(evidenceSummary.replacement.textContent.includes("School history"));
    assert.ok(evidenceSummary.replacement.textContent.includes("Attendance-era locality is unknown"));
    assert.ok(comparisonBody.textContent.includes("Candidate School"));
    assert.equal(comparisonBody.children.length, 3);
    assert.ok(comparisonBody.textContent.includes("ACARA identifier"));
    assert.equal(comparisonBody.textContent.includes("state"), false);
    assert.equal(additionalComparisonBody.children.length, 6);
    assert.ok(additionalComparisonBody.textContent.includes("state"));
    assert.equal(additionalComparison.hidden, false);
    assert.ok(
      additionalComparisonSummary.textContent.includes("6 more · 4 unknown"),
      additionalComparisonSummary.textContent,
    );
  } else if (scenario === "evidence_copy") {
    action.value = "research";
    document.emit("apemap:school-evidence-copy", {
      value: "acara:789", evidence_id: "evidence-1", source_url: "https://example.org/history",
    });
    assert.equal(ref.value, "acara:789");
    assert.equal(JSON.parse(payload.value).institution_ref, "acara:789");
    assert.equal(source.value, "https://example.org/history");
    assert.equal(evidence.checked, true);
    assert.equal(action.value, "research");
    assert.equal(workspace.dataset.dirty, "true");
  }
  assert.equal(submissions, scenario === "fallback_candidate" ? 1 : 0);
}
main().catch(error => { console.error(error); process.exitCode = 1; });
"""
).replace(
    'assert.equal(url.pathname, "/institutions/lookup");',
    'assert.ok(["/institutions/lookup", "/comparison"].includes(url.pathname));',
)


@pytest.mark.parametrize(
    "scenario",
    [
        "selection",
        "edits",
        "evidence_selection",
        "search",
        "debounce_stale",
        "empty_timeout",
        "failed_no_results",
        "external_controls",
        "associated_outcome",
        "external_candidate",
        "fallback_candidate",
        "comparison_evidence",
        "evidence_copy",
    ],
)
def test_school_chooser_script(scenario: str) -> None:
    executable = shutil.which("node")
    if executable is None:
        pytest.skip("Node.js required for offline school chooser interactions")
    result = subprocess.run(
        [executable, "-e", HARNESS, str(SCRIPT), scenario],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
