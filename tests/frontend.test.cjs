const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const {test} = require("node:test");
const vm = require("node:vm");

const source = readFileSync(`${__dirname}/../adrobot/static/app.js`, "utf8");

function element() {
  return {
    children: [], value: "", disabled: false,
    append(...children) { this.children.push(...children); },
    add(child) { this.children.push(child); },
    replaceChildren(...children) { this.children = children; },
  };
}

function editor(offers) {
  const elements = new Map();
  const get = (selector) => {
    if (!elements.has(selector)) elements.set(selector, element());
    return elements.get(selector);
  };
  const context = vm.createContext({
    document: {querySelector: get, querySelectorAll: () => [], createElement: element},
    Option: function(text, value) { this.text = text; this.value = value; },
    sessionStorage: {getItem: () => null},
    fetch: () => new Promise(() => {}),
    structuredClone,
    initialOffers: offers,
  });
  vm.runInContext(source, context);
  vm.runInContext(`
    configuration = {connected: true, writes_enabled: true, editor_enabled: true};
    catalogs = {offers: [1, 2, 3].map(id => ({id, name: String(id)}))};
    selectedStream = {schema: "landings", offers: structuredClone(initialOffers)};
    snapshot = {editable: true};
    draft = structuredClone(initialOffers);
    renderOffers();
  `, context);
  return {
    add(id) {
      get("#add-offer-select").value = String(id);
      get("#add-offer").onclick();
    },
    remove(index) {
      const row = get("#offer-rows").children[index];
      const button = row.children[3].children[0];
      assert.equal(button.disabled, false);
      button.onclick();
    },
    rows() {
      return JSON.parse(vm.runInContext("JSON.stringify(draft)", context));
    },
    saveDisabled() { return get("#save-offers").disabled; },
  };
}

test("returning a disabled binding preserves its state and preview shares", () => {
  const form = editor([
    {offer_id: 1, share: 100, state: "active"},
    {offer_id: 2, share: 0, state: "disabled"},
  ]);
  form.remove(1);
  form.add(2);
  form.add(3);
  assert.deepEqual(form.rows(), [
    {offer_id: 1, share: 50, state: "active"},
    {offer_id: 2, share: 0, state: "disabled"},
    {offer_id: 3, share: 50, state: "active"},
  ]);
  assert.equal(form.saveDisabled(), false);
});

test("undoing an addition restores the persisted uneven shares", () => {
  const original = [
    {offer_id: 1, share: 90, state: "active"},
    {offer_id: 2, share: 10, state: "active"},
  ];
  const form = editor(original);
  form.add(3);
  assert.deepEqual(form.rows().map(row => row.share), [33, 33, 34]);
  form.remove(2);
  assert.deepEqual(form.rows(), original);
  assert.equal(form.saveDisabled(), true);
});

test("undoing removal restores the original binding and shares", () => {
  const original = [
    {offer_id: 1, share: 70, state: "active"},
    {offer_id: 2, share: 30, state: "active"},
  ];
  const form = editor(original);
  form.remove(1);
  assert.equal(form.rows()[0].share, 100);
  form.add(2);
  assert.deepEqual(form.rows(), original);
  assert.equal(form.saveDisabled(), true);
});
