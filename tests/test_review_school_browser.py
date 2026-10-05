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
Element.prototype.querySelectorAll = function(selector) {
  return this.queries.get(selector) || [];
};
const form = new Element("form");
let submissions = 0;
form.submit = form.requestSubmit = () => { submissions++; };
const ref = new Element("input");
ref.value = "acara:99";
const payload = new Element("textarea");
payload.value = JSON.stringify({recorded_name: "Original School", institution_ref: "acara:99", extra: {retained: true}});
const source = new Element("input"); source.value = "https://example.org/original";
const selected = new Element("p");
const lookup = new Element("div"); lookup.dataset.lookupUrl = "/institutions/lookup";
const query = new Element("input");
const results = new Element("ul");
const status = new Element("p");
const searchButton = new Element("button");
const searchDetails = new Element("details");
const save = new Element("button");
const previewStatus = new Element("p");
const choose = new Element("button");
choose.dataset.chooseSchool = "acara:123"; choose.dataset.schoolName = "Selected School";
const useSource = new Element("button"); useSource.dataset.useSource = "https://example.org/chosen";
const find = new Element("button"); find.dataset.findSchool = "School lead";
form.append(ref, payload, source, selected, lookup, searchDetails, choose, useSource, find);
lookup.append(query, results, status, searchButton);
for (const [selector, element] of [
  ['[name="field_institution_ref"]', ref], ['[name="payload"]', payload],
  ['[name="source_url"]', source], ['[data-selected-school]', selected],
  ['[data-school-lookup]', lookup], ['[data-school-search]', searchDetails],
  ['[data-choose-school]', [choose]], ['[data-use-source]', [useSource]],
  ['[data-find-school]', [find]],
]) form.queries.set(selector, element);
for (const [selector, element] of [
  ['[data-school-query]', query], ['[data-school-results]', results],
  ['[data-school-search-status]', status], ['[data-school-search-button]', searchButton],
]) lookup.queries.set(selector, element);
const document = {
  querySelector(selector) {
    return new Map([['[data-school-draft]', form], ['[data-save-school]', save],
      ['[data-preview-status]', previewStatus]]).get(selector);
  },
  createElement(tagName) { return new Element(tagName); },
};
"""
    + "let now ="
    + BROWSER_HARNESS.split("let now =")[1].split("vm.runInNewContext")[0]
    + r"""
vm.runInNewContext(fs.readFileSync(scriptPath, "utf8"), {
  document, window: {location: {href: "http://127.0.0.1:8765/items/school:fixture"}},
  URL, AbortController, fetch: fetchLocal, setTimeout: setTimer,
  clearTimeout: id => timers.delete(id),
}, {filename: scriptPath});
function buttons(node) { return node.children.flatMap(child => [
  ...(child.tagName === "button" ? [child] : []), ...buttons(child)]); }
async function answer(index, rows) {
  requests[index].resolve({ok: true, json: async () => ({results: rows})});
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
  }
  assert.equal(submissions, 0);
}
main().catch(error => { console.error(error); process.exitCode = 1; });
"""
)


@pytest.mark.parametrize(
    "scenario",
    [
        "selection",
        "edits",
        "search",
        "debounce_stale",
        "empty_timeout",
        "failed_no_results",
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
