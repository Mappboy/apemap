"""Exercise the shipped lookup JavaScript offline with a small DOM and clock."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

LOOKUP_SCRIPT = (
    Path(__file__).resolve().parents[1] / "apemap/review/static/institution-lookup.js"
)

BROWSER_HARNESS = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const scriptPath = process.argv[1];
const scenario = process.argv[2];

class Element {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.parentElement = null;
    this.listeners = new Map();
    this.queries = new Map();
    this.dataset = {};
    this.value = "";
    this.hidden = false;
    this.type = tagName === "button" ? "submit" : "";
    this._text = "";
  }
  set textContent(value) {
    this._text = String(value);
    this.children = [];
  }
  get textContent() {
    return this._text + this.children.map(child => child.textContent).join("");
  }
  set innerHTML(value) {
    throw new Error(`Lookup must render register text safely: ${value}`);
  }
  append(...children) {
    for (const child of children) {
      assert.ok(child instanceof Element, "The DOM stub accepts element nodes");
      child.parentElement = this;
      this.children.push(child);
    }
  }
  replaceChildren(...children) {
    this.children = [];
    this._text = "";
    this.append(...children);
  }
  closest(tagName) {
    for (let node = this; node; node = node.parentElement) {
      if (node.tagName === tagName) return node;
    }
    return null;
  }
  querySelector(selector) {
    assert.ok(this.queries.has(selector), `Unexpected selector: ${selector}`);
    return this.queries.get(selector);
  }
  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }
  emit(type, properties = {}) {
    const event = {
      type,
      target: this,
      defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; },
      ...properties,
    };
    for (const listener of this.listeners.get(type) || []) listener(event);
    if (!event.defaultPrevented && (
      (type === "click" && this.type === "submit") ||
      (type === "keydown" && event.key === "Enter")
    )) this.closest("form").requestSubmit();
    return event;
  }
}

const form = new Element("form");
let submissions = 0;
form.submit = form.requestSubmit = () => { submissions += 1; };
const widget = new Element("section");
widget.dataset.lookupUrl = "/institutions/lookup";
const query = new Element("input");
const searchButton = new Element("button");
searchButton.type = "button";
const status = new Element("p");
const results = new Element("ul");
widget.append(query, searchButton, status, results);
widget.queries.set("[data-lookup-query]", query);
widget.queries.set("[data-lookup-search]", searchButton);
widget.queries.set("[data-lookup-status]", status);
widget.queries.set("[data-lookup-results]", results);
const reference = new Element("input");
reference.value = "acara:99";
const payload = new Element("textarea");
const originalPayload = {
  recorded_name: "Originally recorded school",
  institution_ref: "acara:99",
  relationship_type: "successor",
  attended_status: "assumed",
  reviewer_notes: "Preserve these notes",
  additional: { retained: true },
};
payload.value = JSON.stringify(originalPayload, null, 2);
const payloadMode = new Element("input");
payloadMode.value = "json";
const source = new Element("input");
source.value = "https://example.edu.au/evidence";
const reviewer = new Element("input");
reviewer.value = "Fixture Reviewer";
const notes = new Element("textarea");
notes.value = "An unfinished review draft";
form.append(widget, reference, payload, payloadMode, source, reviewer, notes);
form.queries.set('[name="field_institution_ref"]', reference);
form.queries.set('textarea[name="payload"]', payload);

const document = {
  querySelectorAll(selector) {
    assert.equal(selector, "[data-institution-lookup]");
    return [widget];
  },
  createElement(tagName) { return new Element(tagName); },
};

let now = 0;
let nextTimer = 1;
const timers = new Map();
function setTimer(callback, delay) {
  const id = nextTimer++;
  timers.set(id, { callback, at: now + delay });
  return id;
}
function advance(milliseconds) {
  now += milliseconds;
  while (true) {
    const due = [...timers.entries()]
      .filter(([, timer]) => timer.at <= now)
      .sort((left, right) => left[1].at - right[1].at || left[0] - right[0]);
    if (!due.length) return;
    const [id, timer] = due[0];
    timers.delete(id);
    timer.callback();
  }
}

const requests = [];
function fetchLocal(input, options = {}) {
  const url = new URL(String(input));
  assert.equal(url.origin, "http://127.0.0.1:8765");
  assert.equal(url.pathname, "/institutions/lookup");
  assert.equal(options.method || "GET", "GET");
  assert.equal(options.credentials, "same-origin");
  return new Promise((resolve, reject) => {
    requests.push({ url, options, resolve, reject });
  });
}

vm.runInNewContext(fs.readFileSync(scriptPath, "utf8"), {
  document,
  window: { location: { href: "http://127.0.0.1:8765/items/school:fixture" } },
  URL,
  AbortController,
  fetch: fetchLocal,
  setTimeout: setTimer,
  clearTimeout: id => timers.delete(id),
}, { filename: scriptPath });

const institutions = [
  {
    institution_ref: "acara:123",
    acara_id: "123",
    school_name: "Fixture <img src=x onerror=alert(1)> College",
    state: "NSW",
    suburb: "Sydney",
    sector: "Government",
    school_type: "Secondary",
    institution_status: "current",
  },
  {
    institution_ref: "acara:456",
    acara_id: "456",
    school_name: "Fixture <img src=x onerror=alert(1)> College",
    state: "TAS",
    suburb: "Hobart",
    sector: "Independent",
    school_type: "Combined",
    institution_status: "historical_only",
  },
];

function draft() {
  return {
    reference: reference.value,
    payload: payload.value,
    payloadMode: payloadMode.value,
    source: source.value,
    reviewer: reviewer.value,
    notes: notes.value,
  };
}
function buttons(node) {
  return node.children.flatMap(child => [
    ...(child.tagName === "button" ? [child] : []),
    ...buttons(child),
  ]);
}
function type(value) {
  query.value = value;
  return query.emit("input");
}
function search(value) {
  query.value = value;
  searchButton.emit("click");
}
async function settle() {
  await new Promise(resolve => setImmediate(resolve));
}
async function answer(index, rows, options = {}) {
  requests[index].resolve({
    ok: options.ok ?? true,
    json: async () => options.body ?? { results: rows },
  });
  await settle();
}

async function main() {
  const originalDraft = draft();
  if (scenario === "debounce") {
    type("Fixture");
    advance(200);
    assert.equal(requests.length, 0);
    type("Fixture College");
    advance(249);
    assert.equal(requests.length, 0);
    advance(1);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url.searchParams.get("q"), "Fixture College");
    await answer(0, institutions);
    assert.equal(buttons(results).length, 2);
    assert.deepEqual(draft(), originalDraft);
  } else if (scenario === "select") {
    search("  Fixture & College  ");
    assert.equal(requests[0].url.searchParams.get("q"), "Fixture & College");
    await answer(0, institutions);
    assert.equal(results.hidden, false);
    for (const detail of [
      institutions[0].school_name, "acara:123", "NSW", "Sydney", "Government",
      "Secondary", "acara:456", "TAS", "Hobart", "Independent", "Combined",
      "historical only",
    ]) assert.ok(results.textContent.includes(detail), detail);
    const choose = buttons(results);
    assert.equal(choose.length, 2);
    assert.ok(choose.every(button => button.type === "button"));
    choose[1].emit("click");
    assert.equal(reference.value, "acara:456");
    assert.deepEqual(JSON.parse(payload.value), {
      ...originalPayload, institution_ref: "acara:456",
    });
    assert.deepEqual({ ...draft(), reference: originalDraft.reference,
      payload: originalDraft.payload }, originalDraft);
    assert.equal(requests.length, 1, "Choosing must not save or fetch");
    assert.ok(status.textContent.includes("preview"));
  } else if (scenario === "invalid_json") {
    search("Fixture");
    await answer(0, institutions);
    for (const invalid of ["{unfinished", "[]", "null", '"scalar"']) {
      payload.value = invalid;
      buttons(results)[0].emit("click");
      assert.equal(payload.value, invalid);
      assert.equal(reference.value, "acara:123");
      assert.ok(status.textContent.includes("JSON"));
      assert.ok(status.textContent.includes("unchanged"));
    }
    assert.equal(payloadMode.value, originalDraft.payloadMode);
    assert.equal(notes.value, originalDraft.notes);
    assert.equal(requests.length, 1);
  } else if (scenario === "empty") {
    type("   ");
    advance(250);
    searchButton.emit("click");
    assert.equal(requests.length, 0);
    assert.equal(results.children.length, 0);
    assert.equal(results.hidden, true);
    assert.deepEqual(draft(), originalDraft);
  } else if (scenario === "no_results") {
    search("Unknown school");
    await answer(0, []);
    assert.equal(results.children.length, 0);
    assert.equal(results.hidden, true);
    assert.ok(status.textContent.includes("No matching school"));
    assert.deepEqual(draft(), originalDraft);
  } else if (scenario === "stale_response") {
    search("First school");
    search("Second school");
    assert.equal(requests.length, 2);
    assert.equal(requests[0].options.signal.aborted, true);
    await answer(1, [{ ...institutions[1], school_name: "Newest result" }]);
    const newestStatus = status.textContent;
    await answer(0, [{ ...institutions[0], school_name: "Stale result" }]);
    assert.ok(results.textContent.includes("Newest result"));
    assert.equal(results.textContent.includes("Stale result"), false);
    assert.equal(status.textContent, newestStatus);
    assert.deepEqual(draft(), originalDraft);
  } else if (scenario === "enter") {
    type("Fixture");
    const event = query.emit("keydown", { key: "Enter" });
    assert.equal(event.defaultPrevented, true);
    assert.equal(requests.length, 1);
    advance(250);
    assert.equal(requests.length, 1, "Enter cancels the pending debounce");
    await answer(0, institutions);
    assert.deepEqual(draft(), originalDraft);
  } else if (scenario === "failed_request") {
    search("Fixture");
    await answer(0, [], { ok: false, body: { error: "Register unavailable" } });
    assert.equal(results.children.length, 0);
    assert.equal(results.hidden, true);
    assert.ok(status.textContent.includes("Register unavailable"));
    assert.deepEqual(draft(), originalDraft);
  } else if (scenario === "timeout") {
    search("Fixture");
    advance(59999);
    assert.equal(requests[0].options.signal.aborted, false);
    assert.deepEqual(draft(), originalDraft);
    advance(1);
    assert.equal(requests[0].options.signal.aborted, true);
    const error = new Error("Request aborted");
    error.name = "AbortError";
    requests[0].reject(error);
    await settle();
    assert.ok(status.textContent.includes("timed out"));
    assert.equal(results.children.length, 0);
    assert.equal(results.hidden, true);
    assert.deepEqual(draft(), originalDraft);
  } else {
    throw new Error(`Unknown scenario: ${scenario}`);
  }
  assert.equal(submissions, 0, "Lookup and choosing cannot submit the review form");
}

main().catch(error => { console.error(error); process.exitCode = 1; });
"""


@pytest.fixture(scope="module")
def node_executable() -> str:
    executable = shutil.which("node")
    if executable is None:
        pytest.skip("Node.js is required to execute the offline lookup script test")
    return executable


@pytest.mark.unit
@pytest.mark.parametrize(
    "scenario",
    (
        "debounce",
        "select",
        "invalid_json",
        "empty",
        "no_results",
        "stale_response",
        "enter",
        "failed_request",
        "timeout",
    ),
)
def test_lookup_browser_contract(node_executable: str, scenario: str) -> None:
    result = subprocess.run(
        [node_executable, "-e", BROWSER_HARNESS, str(LOOKUP_SCRIPT), scenario],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"Lookup scenario {scenario!r} failed:\n{result.stdout}\n{result.stderr}"
    )
