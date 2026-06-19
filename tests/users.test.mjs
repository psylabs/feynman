import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

class FakeClassList {
  constructor() {
    this.values = new Set();
  }
  add(name) { this.values.add(name); }
  remove(name) { this.values.delete(name); }
  toggle(name) {
    if (this.values.has(name)) {
      this.values.delete(name);
      return false;
    }
    this.values.add(name);
    return true;
  }
  contains(name) { return this.values.has(name); }
}

class FakeElement {
  constructor(id = "") {
    this.id = id;
    this.textContent = "";
    this.innerHTML = "";
    this.children = [];
    this.className = "";
    this.classList = new FakeClassList();
    this.listeners = {};
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  addEventListener(event, handler) {
    this.listeners[event] = handler;
  }
  closest() {
    return null;
  }
}

function createLocalStorage(initial = {}) {
  const store = new Map(Object.entries(initial));
  return {
    getItem: (key) => store.get(key) || null,
    setItem: (key, value) => { store.set(key, String(value)); },
    removeItem: (key) => { store.delete(key); },
    dump: () => Object.fromEntries(store),
  };
}

function loadUsersModule({ storage = {}, fetchImpl }) {
  const elements = {
    "btn-user": new FakeElement("btn-user"),
    "user-menu": new FakeElement("user-menu"),
  };
  const windowListeners = {};
  const documentListeners = {};
  const context = {
    console,
    CustomEvent: function CustomEvent(type, init) {
      return { type, detail: init?.detail };
    },
    localStorage: createLocalStorage(storage),
    fetch: fetchImpl,
    alert() {},
    prompt() { return ""; },
    window: {
      addEventListener: (event, handler) => { windowListeners[event] = handler; },
      dispatchEvent(event) {
        windowListeners[event.type]?.(event);
      },
    },
    document: {
      hidden: false,
      getElementById: (id) => elements[id],
      createElement: () => new FakeElement(),
      addEventListener: (event, handler) => { documentListeners[event] = handler; },
    },
  };
  vm.runInNewContext(readFileSync("web/users.js", "utf8"), context);
  return {
    api: context.window.feynmanUser,
    button: elements["btn-user"],
    menu: elements["user-menu"],
    storage: context.localStorage,
    windowListeners,
    documentListeners,
  };
}

function okJson(value) {
  return { ok: true, json: async () => value };
}

test("uses stored user id when initial /users fetch fails", async () => {
  const loaded = loadUsersModule({
    storage: { "feynman.user_id": "u1" },
    fetchImpl: async () => { throw new Error("offline"); },
  });

  await loaded.api.ready;

  assert.equal(loaded.api.getCurrent().id, "u1");
  assert.equal(loaded.api.getCurrent().name, "You");
  assert.equal(loaded.button.textContent, "You ▾");
});

test("successful /users fetch stores a cached user snapshot", async () => {
  const loaded = loadUsersModule({
    storage: { "feynman.user_id": "u1" },
    fetchImpl: async () => okJson([{ id: "u1", name: "Pip", has_completed_eval: true }]),
  });

  await loaded.api.ready;

  const cached = JSON.parse(loaded.storage.getItem("feynman.users_cache"));
  assert.equal(loaded.api.getCurrent().name, "Pip");
  assert.equal(cached[0].name, "Pip");
  assert.equal(loaded.button.textContent, "Pip ▾");
});

test("retry after failed user fetch replaces fallback with server users", async () => {
  let online = false;
  const loaded = loadUsersModule({
    storage: { "feynman.user_id": "u1" },
    fetchImpl: async () => {
      if (!online) throw new Error("offline");
      return okJson([{ id: "u1", name: "Pip", has_completed_eval: true }]);
    },
  });
  await loaded.api.ready;
  assert.equal(loaded.api.getCurrent().name, "You");

  online = true;
  await loaded.windowListeners.online();

  assert.equal(loaded.api.getCurrent().name, "Pip");
  assert.equal(loaded.button.textContent, "Pip ▾");
});
