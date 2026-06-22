import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

// app.js runs init at load and touches a lot of DOM. Auto-vivify an element
// for any id and stub the few globals it references, then read the pure
// helper it exposes (window.isDeadCapture).
function loadAppModule() {
  const make = () => ({
    textContent: "", innerHTML: "", value: "", disabled: false, className: "",
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    children: [], appendChild(c) { this.children.push(c); return c; },
    addEventListener() {}, removeEventListener() {}, focus() {}, closest() { return null; },
    setPointerCapture() {}, releasePointerCapture() {}, querySelector() { return null; },
  });
  const els = new Map();
  const context = {
    console,
    setTimeout, clearTimeout, setInterval, clearInterval,
    requestAnimationFrame: () => 0, cancelAnimationFrame() {},
    fetch: async () => ({ ok: true, json: async () => ({}) }),
    CustomEvent: function (type, init) { return { type, detail: init?.detail }; },
    Audio: function () { return { play: async () => {}, addEventListener() {}, playbackRate: 1 }; },
    navigator: { userAgent: "test", mediaDevices: {} },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    document: {
      body: { classList: { add() {}, remove() {}, toggle() {} } },
      getElementById: (id) => { if (!els.has(id)) els.set(id, make()); return els.get(id); },
      createElement: make,
      addEventListener() {},
      querySelectorAll: () => [],
    },
    window: {
      addEventListener() {}, matchMedia: () => ({ matches: false }),
    },
  };
  context.window.window = context.window;
  vm.runInNewContext(readFileSync("web/app.js", "utf8"), context);
  return context.window.isDeadCapture;
}

const isDeadCapture = loadAppModule();

test("flags a header-only (~110 byte) clip as dead", () => {
  assert.equal(isDeadCapture({ bytes: 110, energy: { frames: 25, peak_rms: 0 } }), true);
});

test("flags recorded near-silence (real bytes, peak below floor) as dead", () => {
  assert.equal(isDeadCapture({ bytes: 11588, energy: { frames: 107, peak_rms: 0.0076 } }), true);
});

test("does NOT flag a healthy answer clip", () => {
  assert.equal(isDeadCapture({ bytes: 11157, energy: { frames: 144, peak_rms: 0.044 } }), false);
});

test("does NOT flag when metering failed to init (no energy frames)", () => {
  // Avoid false-rejecting a real answer just because WebAudio didn't measure it.
  assert.equal(isDeadCapture({ bytes: 12000, energy: { frames: 0 } }), false);
  assert.equal(isDeadCapture({ bytes: 12000 }), false);
});

test("guards against a null/empty meta", () => {
  assert.equal(isDeadCapture(null), false);
});
