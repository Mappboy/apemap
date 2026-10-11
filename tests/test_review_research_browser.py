"""Exercise the cited research panel with a deterministic local DOM and fetch stub."""

from pathlib import Path
import shutil
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "apemap/review/static/research.js"

HARNESS = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const scriptPath = process.argv[1];
class TextNode { constructor(text) { this.textContent = text; this.parent = null; this.children = []; } matches() { return false; } }
class Element {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.parent = null; this.dataset = {}; this.attributes = {}; this.listeners = {}; this.value = ""; this.checked = false; this.hidden = false; this.disabled = false; this.name = ""; this.type = ""; this.rel = ""; this.href = ""; this.action = ""; this.formAction = ""; }
  append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); } }
  after(node) { if (this.parent) { const index = this.parent.children.indexOf(this); node.parent = this.parent; this.parent.children.splice(index + 1, 0, node); } }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
  dispatchEvent(event) { event.target = this; for (const callback of this.listeners[event.type] || []) callback(event); if (event.bubbles) for (const callback of docListeners[event.type] || []) callback(event); return true; }
  async emit(type, extra = {}) { const event = { target: this, preventDefault() { this.defaultPrevented = true; }, ...extra }; for (const callback of this.listeners[type] || []) await callback(event); return event; }
  get textContent() { return this.children.map(child => child.textContent || "").join(""); }
  set textContent(value) { this.replaceChildren(new TextNode(String(value ?? ""))); }
  matches(selector) {
    if (selector === "[data-research-start]") return Object.hasOwn(this.dataset, "researchStart");
    if (selector === "[data-research-inspect]") return Object.hasOwn(this.dataset, "researchInspect");
    if (selector === "[data-member-retained]") return Object.hasOwn(this.dataset, "memberRetained");
    const nameMatch = selector.match(/^\[name=[\"']([^\"']+)[\"']\]$/);
    if (nameMatch) return this.name === nameMatch[1];
    return false;
  }
  contains(node) { return this === node || this.children.some(child => child.contains?.(node)); }
  closest(selector) {
    let item = this;
    while (item) {
      if (selector === "[data-dismiss-suggestion]" && Object.hasOwn(item.dataset, "dismissSuggestion")) return item;
      if (selector === "[data-copy-evidence]" && Object.hasOwn(item.dataset, "copyEvidence")) return item;
      if (selector === "[data-suggestion]" && Object.hasOwn(item.dataset, "suggestion")) return item;
      item = item.parent;
    }
    return null;
  }
  querySelector(selector) {
    if (this.queries?.has(selector)) return this.queries.get(selector);
    return descendants(this).find(item => item.matches(selector)) || null;
  }
}
function descendants(parent) { return (parent.children || []).flatMap(item => [item, ...descendants(item)]); }
function findData(parent, key) { return descendants(parent).find(item => item.dataset && Object.hasOwn(item.dataset, key)); }
const panels = [];
function makePanel(reviewId, selectedProvider) {
  const panel = new Element("section"); panel.dataset.reviewId = reviewId; panel.queries = new Map();
  const provider = new Element("select"); provider.name = `research_provider_id_${reviewId.split(":").at(-1)}`; provider.value = selectedProvider;
  const status = new Element("p"), results = new Element("div"), editor = new Element("div"), feedback = new Element("div"), csrf = new Element("input"); csrf.name = "csrf_token"; csrf.value = "csrf-test-token";
  panel.queries.set("[data-research-status]", status); panel.queries.set("[data-research-results]", results); panel.queries.set("[data-evidence-editor]", editor); panel.queries.set("[data-evidence-feedback]", feedback); panel.queries.set('[name="csrf_token"]', csrf); panel.queries.set('input[name="csrf_token"]', csrf); panel.queries.set("[data-research-provider]", provider);
  panel.append(csrf, status, results, editor, feedback); panels.push(panel); return panel;
}
const first = makePanel("member:test:gender", "first-provider");
const second = makePanel("member:test:date_of_birth", "selected-provider");
const third = makePanel("member:test:wikidata_id", "third-provider");
const memberForm = new Element("form"); memberForm.action = "/members/TEST/preview";
const memberAction = new Element("select"); memberAction.name = "action_gender"; memberAction.value = "unchanged"; memberForm.append(memberAction);
const providers = panels.map(panel => panel.querySelector("[data-research-provider]")); providers.forEach(item => memberForm.append(item));
const docListeners = {}; const document = {
  querySelectorAll(selector) { return selector === "[data-research-panel]" ? panels : []; },
  getElementById() { return null; },
  createElement(tag) { return new Element(tag); },
  createTextNode(text) { return new TextNode(text); },
  addEventListener(type, callback) { (docListeners[type] ||= []).push(callback); },
};
class FormDataMock {
  constructor(form) { this.values = []; for (const node of descendants(form)) if (node.name && (node.type !== "checkbox" || node.checked)) this.values.push([node.name, node.value]); }
  set(name, value) { this.values = this.values.filter(row => row[0] !== name); this.values.push([name, value]); }
  append(name, value) { this.values.push([name, value]); }
  get(name) { return this.values.find(row => row[0] === name)?.[1] ?? null; }
}
const timers = [];
const timer = callback => { timers.push(callback); return timers.length; };
const fetchCalls = []; let statusCount = 0; let inspectCount = 0; let previewCount = 0; let saveCount = 0;
async function fetch(url, options = {}) {
  fetchCalls.push({url: String(url), options});
  if (String(url).endsWith("/research")) return {ok: true, json: async () => ({job_id: "job-2", detail_url: "/job/job-2", status_url: "/job/job-2/status"})};
  if (String(url).endsWith("/status")) {
    statusCount++;
    if (statusCount === 1) return {ok: true, json: async () => ({status: "running"})};
    return {ok: true, json: async () => ({status: "complete", suggestions: [{source_title: "Biography <img src=x>", source_url: "javascript:alert(1)", stance: "supports", claim_type: "member_value", claim_value: {field: "date_of_birth", value: "1972-02-03"}, excerpt_or_note: "Cited value", retrieved_at: "2026-10-10T00:00:00+00:00"}]})};
  }
  if (String(url).includes("/suggestions/0")) { inspectCount++; return {ok: true, json: async () => ({suggestion: {}, evidence_values: {evidence_source_title: "Official biography", evidence_source_type: "assisted-search", evidence_source_url: "https://example.org/bio", evidence_retrieved_at: "2026-10-10T00:00:00+00:00", evidence_claim_type: "member_value", evidence_claim_value: {field: "date_of_birth", value: "1972-02-03"}, evidence_stance: "supports", evidence_excerpt_or_note: "Cited value", research_job_id: "job-2", research_suggestion_index: "0"}})}; }
  if (String(url).endsWith("/evidence/preview")) { previewCount++; return {ok: true, json: async () => ({evidence_preview_token: "signed-preview", validation: [], record: {source_title: "Official biography"}})}; }
  if (String(url).endsWith("/evidence/save")) { saveCount++; return {ok: true, json: async () => ({record: {evidence_id: "evidence-1", source_title: "Official biography", source_url: "https://example.org/bio", stance: "supports", claim_type: "member_value", claim_value: {field: "date_of_birth", value: "1972-02-03"}}})}; }
  if (String(url).endsWith("/evidence/evidence-1/copy")) return {ok: true, json: async () => ({value: "1972-02-03", field: "date_of_birth", evidence_id: "evidence-1", source_url: "https://example.org/bio"})};
  throw new Error(`Unexpected local URL: ${url}`);
}
const emitted = [];
document.addEventListener("apemap:member-evidence-copy", event => emitted.push(event.detail));
class CustomEventMock { constructor(type, options) { this.type = type; Object.assign(this, options); } }
const window = {location: {href: "http://127.0.0.1/members/TEST"}, setTimeout: timer};
vm.runInNewContext(fs.readFileSync(scriptPath, "utf8"), {document, window, URL, URLSearchParams, FormData: FormDataMock, CustomEvent: CustomEventMock, fetch, setTimeout: timer, console}, {filename: scriptPath});
async function flushTimers() { while (timers.length) { const next = timers.shift(); await next(); await new Promise(resolve => setImmediate(resolve)); } }
function submit(form, submitter) { const event = {target: form, submitter, prevented: false, preventDefault() { this.prevented = true; }}; for (const callback of docListeners.submit || []) callback(event); return event; }
async function main() {
  const start = new Element("button"); start.dataset.researchReviewId = second.dataset.reviewId; start.formAction = "/items/member%3Atest%3Adate_of_birth/research"; start.name = "provider_id";
  const started = submit(memberForm, start); await new Promise(resolve => setImmediate(resolve)); assert.equal(started.prevented, true);
  const request = fetchCalls.find(call => call.url.endsWith("/research")); assert.ok(request);
  assert.equal(first.querySelector("[data-research-status]").textContent, "", "a member panel request does not update sibling fields");
  assert.equal(third.querySelector("[data-research-status]").textContent, "", "each field owns its own research state");
  assert.equal(new FormDataMock({children: []}).get("missing"), null);
  const providerRequest = request.options.body; assert.equal(providerRequest.get("provider_id"), "selected-provider");
  assert.equal(providerRequest.get("action_gender"), "unchanged", "the active member draft is submitted with research");
  await flushTimers();
  assert.equal(statusCount, 2);
  const result = second.querySelector("[data-research-results]");
  assert.ok(result.textContent.includes("Biography <img src=x>"));
  assert.equal(descendants(result).some(item => item.tagName === "IMG"), false, "provider titles are rendered as text");
  assert.equal(descendants(result).some(item => item.tagName === "A"), false, "unsafe source URLs are omitted");
  const suggestion = findData(result, "suggestion"); const dismiss = findData(suggestion, "dismissSuggestion");
  await second.emit("click", {target: dismiss}); assert.equal(result.children.length, 0, "dismiss removes the disposable suggestion");

  // Re-run with a safe citation to exercise explicit inspection and retention.
  const render = (call => call)("unused"); void render;
  const inspectForm = new Element("form"); inspectForm.action = "/items/member%3Atest%3Adate_of_birth/research/job-2/suggestions/0"; inspectForm.dataset.researchInspect = "";
  const inspected = new Element("input"); inspected.name = "source_inspected"; inspected.type = "checkbox"; inspected.checked = false; inspectForm.append(inspected); second.append(inspectForm);
  const rejected = submit(inspectForm); assert.equal(rejected.prevented, true); assert.equal(inspectCount, 0, "inspection is required before the evidence draft opens");
  inspected.checked = true; submit(inspectForm); await new Promise(resolve => setImmediate(resolve)); assert.equal(inspectCount, 1);
  const editor = second.querySelector("[data-evidence-editor]"); const previewForm = descendants(editor).find(item => item.dataset && item.dataset.evidencePreview !== undefined);
  assert.ok(previewForm); await previewForm.emit("submit"); assert.equal(previewCount, 1);
  assert.ok(fetchCalls.find(call => call.url.endsWith("/evidence/preview")).options.body.get("csrf_token") === "csrf-test-token");
  const retain = descendants(editor).find(item => item.textContent === "Retain this evidence"); assert.ok(retain);
  await retain.emit("click"); await new Promise(resolve => setImmediate(resolve)); assert.equal(saveCount, 1);
  assert.ok(descendants(second).some(item => item.name === "evidence_refs_date_of_birth" && item.value === "evidence-1"), second.querySelector("[data-evidence-feedback]").textContent);
  const retainedPanel = second.querySelector("[data-member-retained]");
  const disposable = new Element("article"); disposable.dataset.suggestion = ""; const disposableDismiss = new Element("button"); disposableDismiss.dataset.dismissSuggestion = ""; disposable.append(disposableDismiss); second.querySelector("[data-research-results]").append(disposable);
  await second.emit("click", {target: disposableDismiss}); assert.equal(second.querySelector("[data-member-retained]"), retainedPanel, "dismissing a finding preserves retained ledger evidence");
  const copy = descendants(editor).find(item => item.dataset && item.dataset.copyEvidence === "evidence-1"); assert.ok(copy);
  await second.emit("click", {target: copy}); await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(emitted, [{value: "1972-02-03", field: "date_of_birth", evidence_id: "evidence-1", source_url: "https://example.org/bio"}]);
  assert.equal(memberAction.value, "unchanged", "copying evidence never selects accept");
  const copyCall = fetchCalls.find(call => call.url.endsWith("/evidence/evidence-1/copy"));
  assert.equal(new URLSearchParams(copyCall.options.body).get("csrf_token"), "csrf-test-token");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
"""


def test_research_panel_browser_flow() -> None:
    executable = shutil.which("node")
    if executable is None:
        pytest.skip("Node.js is required for deterministic research panel interaction")
    result = subprocess.run(
        [executable, "-e", HARNESS, str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
