"""Offline interaction checks for inspection, scope and dirty-draft protection."""

from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "apemap/review/static/research-batch.js"
HARNESS = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
class Element {
  constructor(value = "") { this.value = value; this.checked = false; this.disabled = false; this.hidden = true; this.textContent = ""; this.dataset = {}; this.listeners = {}; this.one = {}; this.many = {}; }
  querySelector(selector) { const value = this.one[selector]; return typeof value === "function" ? value() : value || null; }
  querySelectorAll(selector) { return this.many[selector] || []; }
  addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
  emit(type, extra = {}) { const event = {defaultPrevented:false, preventDefault() {this.defaultPrevented = true;}, ...extra}; (this.listeners[type] || []).forEach(listener => listener(event)); return event; }
}
const form = new Element(); form.dataset.reviewReady = "true";
const action = new Element("accept"), notes = new Element(), identity = new Element();
const dispositions = [new Element("supports"), new Element("contextual")];
dispositions.forEach(element => element.dataset.evidenceStance = element.value);
const inspections = [new Element(), new Element()];
const warning = new Element(), dirty = new Element(), count = new Element(), save = new Element(), next = new Element();
form.one = {'[name="action"]':action, '[name="notes"]':notes, '[name="identity_error"]':identity, '[data-review-warning]':warning, '[data-inspection-count]':count, '[data-review-dirty]':dirty};
form.many = {'[data-evidence-stance]':dispositions, '[data-source-inspected]':inspections, '[data-record-review]':[save,next]};
const operations = new Element(), individual = new Element("individual"), sampled = new Element("sampled");
individual.checked = true; individual.dataset = {scopeCount:"2", scopeRetained:"true"}; sampled.dataset = {scopeCount:"5",scopeRetained:"false"};
const retain = new Element(), preview = new Element(), revalidate = new Element();
operations.action = "/research-batches/example/retain";
preview.formAction = "/research-batches/example/preview";
revalidate.formAction = "/research-batches/example/revalidate";
operations.one = {'[name="scope"]:checked':() => individual.checked ? individual : sampled, '[data-stage-retain]':retain, '[data-stage-preview]':preview, '[data-stage-revalidate]':revalidate};
const batch = new Element(), apply = new Element(), previewWarning = new Element(), selectedCount = new Element(), retainedStatus = new Element(), link = new Element(), filter = new Element(), hiddenScope = new Element("individual");
batch.one = {'[data-research-review]':form, '#research-batch-operations':operations, '[data-preview-warning]':previewWarning, '[data-selected-count]':selectedCount, '[data-retention-status]':retainedStatus};
batch.many = {'[data-apply-preview]':[apply], '[data-batch-navigation]':[link], '[data-batch-navigation-form], [data-batch-operation]':[filter,operations], 'input[type="hidden"][name="scope"]':[hiddenScope]};
const document = {querySelector:selector => selector === '[data-research-batch]' ? batch : null};
let confirmAnswer = false, confirmCalls = 0;
const window = new Element(); window.confirm = () => { confirmCalls++; return confirmAnswer; };
vm.runInNewContext(fs.readFileSync(process.argv[1],"utf8"), {document,window,queueMicrotask:callback => callback()});
assert.equal(count.textContent,"0 / 2"); assert.equal(save.disabled,true); assert.equal(next.disabled,true);
assert.equal(preview.disabled,false); assert.equal(retain.disabled,false); assert.equal(selectedCount.textContent,"2");
inspections.forEach(input => input.checked = true); form.emit("change");
assert.equal(count.textContent,"2 / 2"); assert.equal(save.disabled,false); assert.equal(dirty.hidden,false); assert.equal(apply.disabled,true);
assert.equal(link.emit("click").defaultPrevented,true,"cancel keeps dirty draft");
assert.equal(window.emit("beforeunload").defaultPrevented,true);
assert.equal(operations.emit("submit").defaultPrevented,true,"batch action cannot silently discard review draft");
dispositions[0].value = "unavailable"; form.emit("change"); assert.equal(save.disabled,true); assert.match(warning.textContent,/unavailable source/);
action.value = "research"; form.emit("change"); assert.equal(save.disabled,false); assert.equal(notes.required,true);
dispositions[0].value = "contradicts"; action.value = "accept"; form.emit("change"); assert.equal(save.disabled,true); assert.match(warning.textContent,/differs from the saved evidence/);
dispositions[0].value = "supports"; identity.checked = true; form.emit("change"); assert.equal(save.disabled,true);
identity.checked = false; form.emit("change"); assert.equal(save.disabled,false); assert.equal(notes.required,false);
form.dataset.acceptReady = "false"; form.emit("change"); assert.equal(save.disabled,true); assert.match(warning.textContent,/identity remains unresolved/);
action.value = "research"; form.emit("change"); assert.equal(save.disabled,false);
form.dataset.acceptReady = "true"; action.value = "accept"; form.emit("change");
assert.equal(form.emit("submit").defaultPrevented,false);
assert.equal(window.emit("beforeunload").defaultPrevented,false,"successful save clears the unload guard");
let previewSubmit = operations.emit("submit", {submitter:preview});
assert.equal(previewSubmit.defaultPrevented,false);
assert.equal(previewSubmit.submitter.formAction || operations.action,"/research-batches/example/preview","normal preview preserves its native submission target");
individual.checked = false; sampled.checked = true; sampled.name = "scope"; operations.emit("change",{target:sampled});
assert.equal(selectedCount.textContent,"5"); assert.equal(hiddenScope.value,"sampled"); assert.equal(retain.disabled,false); assert.equal(preview.disabled,true); assert.equal(revalidate.disabled,true);
assert.match(previewWarning.textContent,/Approval scope changed/);
sampled.dataset.scopeRetained = "true"; operations.emit("change",{target:sampled});
assert.equal(preview.disabled,false); assert.equal(revalidate.disabled,false);
previewSubmit = operations.emit("submit", {submitter:preview});
assert.equal(previewSubmit.defaultPrevented,false);
assert.equal(previewSubmit.submitter.formAction || operations.action,"/research-batches/example/preview","sampled scope cannot route normal preview through revalidation");
const revalidationSubmit = operations.emit("submit", {submitter:revalidate});
assert.equal(revalidationSubmit.defaultPrevented,false);
assert.equal(revalidationSubmit.submitter.formAction || operations.action,"/research-batches/example/revalidate");
form.emit("input"); confirmAnswer = true; assert.equal(link.emit("click").defaultPrevented,false);
assert.equal(window.emit("beforeunload").defaultPrevented,false,"confirmed navigation does not prompt a second time");
assert.ok(confirmCalls >= 3);
form.dataset.reviewReady = "false"; form.emit("change"); assert.equal(save.disabled,true,"an incomplete or applied batch remains blocked");
"""


def test_research_batch_browser_flow() -> None:
    executable = shutil.which("node")
    if executable is None:
        pytest.skip("Node.js is required for offline batch interaction checks")
    result = subprocess.run(
        [executable, "-e", HARNESS, str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
