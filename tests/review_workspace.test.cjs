const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function runWorkspace() {
  const listeners = {};
  const formListeners = {};
  const windowListeners = {};
  class HTMLButtonElement { constructor() { this.disabled = false; this.dataset = {}; this.attrs = {}; } setAttribute(k, v) { this.attrs[k] = v; } }
  const saves = [new HTMLButtonElement()];
  const preview = { clicked: 0, disabled: false, dataset: {}, hasAttribute: () => false, click() { this.clicked++; } };
  const summary = { focused: 0, focus() { this.focused++; } };
  const disclosure = { open: false, scrollIntoView() {}, querySelector: () => summary };
  class HTMLFormElement { matches(s) { return s.includes('#member-batch-form'); } addEventListener(name, cb) { formListeners[name] = cb; } }
  const form = new HTMLFormElement();
  const workspace = { dataset: {}, setAttribute() {} };
  const document = {
    querySelector: (s) => s === '[data-review-workspace]' ? workspace : s.includes('preview') ? preview : null,
    querySelectorAll: (s) => s.startsWith('form[data-decision-draft]') ? [form] : s.includes('data-workspace-action="save"') ? saves : s.includes('data-research-disclosure') ? [disclosure] : [],
    addEventListener: (name, cb) => { listeners[name] = cb; },
  };
  class HTMLElement { constructor() { this.form = form; this.name = 'action_gender'; } }
  class Element {}
  class HTMLInputElement {}
  class HTMLAnchorElement {}
  const window = { addEventListener: (name, cb) => { windowListeners[name] = cb; }, location: { assign() {} } };
  const context = { document, window, HTMLElement, HTMLFormElement, Element, HTMLButtonElement, HTMLInputElement, HTMLAnchorElement, queueMicrotask: (cb) => cb() };
  vm.runInNewContext(fs.readFileSync('apemap/review/static/workspace.js', 'utf8'), context);
  return { listeners, formListeners, windowListeners, saves, preview, disclosure, summary, workspace, control: new HTMLElement(), Element };
}

test('decision edits invalidate every save control, including external form-associated fields', () => {
  const state = runWorkspace();
  state.listeners.input({ target: state.control });
  assert.equal(state.saves[0].disabled, true);
  assert.equal(state.saves[0].dataset.invalidated, 'true');
  assert.equal(state.workspace.dataset.dirty, 'true');
});

test('prevented research submits keep the unsaved-draft warning active', () => {
  const state = runWorkspace();
  state.listeners.input({ target: state.control });
  state.formListeners.submit({ defaultPrevented: true });
  const unload = { preventDefault() {}, returnValue: undefined };
  state.windowListeners.beforeunload(unload);
  assert.equal(unload.returnValue, '');
});

test('research-start submitters do not clear dirty state before async interception', () => {
  const state = runWorkspace();
  state.listeners.input({ target: state.control });
  const submitter = { dataset: { researchStart: '', researchReviewId: 'school:fixture' }, matches: () => true };
  state.formListeners.submit({ defaultPrevented: false, submitter });
  assert.equal(state.workspace.dataset.dirty, 'true');
  assert.equal(state.saves[0].disabled, true);
});

test('Alt+Shift+V invokes preview only', () => {
  const state = runWorkspace();
  const event = { key: 'v', altKey: true, shiftKey: true, ctrlKey: false, metaKey: false, isComposing: false, target: null, preventDefault() { this.prevented = true; } };
  state.listeners.keydown(event);
  assert.equal(state.preview.clicked, 1);
  assert.equal(event.prevented, true);
});

test('Alt+Shift+R opens the research disclosure without starting a search', () => {
  const state = runWorkspace();
  const event = { key: 'r', altKey: true, shiftKey: true, ctrlKey: false, metaKey: false, isComposing: false, target: null, preventDefault() { this.prevented = true; } };
  state.listeners.keydown(event);
  assert.equal(state.disclosure.open, true);
  assert.equal(state.summary.focused, 1);
  assert.equal(event.prevented, true);
});

test('keyboard shortcuts ignore inherited and plaintext contenteditable editors and IME', () => {
  const state = runWorkspace();
  const editorElement = new state.Element();
  editorElement.isContentEditable = true;
  editorElement.matches = () => false;
  editorElement.closest = () => null;
  const editor = new state.Element();
  const makeEvent = (target, isComposing = false) => ({ key: 'v', altKey: true, shiftKey: true, ctrlKey: false, metaKey: false, isComposing, target, preventDefault() { this.prevented = true; } });
  const blankEditable = makeEvent(editorElement);
  state.listeners.keydown(blankEditable);
  const composing = makeEvent(editor, true);
  state.listeners.keydown(composing);
  assert.equal(state.preview.clicked, 0);
  assert.equal(blankEditable.prevented, undefined);
  assert.equal(composing.prevented, undefined);
});
