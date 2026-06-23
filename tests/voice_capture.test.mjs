import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

// app.js runs init at load and touches a lot of DOM. Auto-vivify an element
// for any id and stub the few globals it references, then read the pure
// helper it exposes (window.isDeadCapture).
function loadAppModule({ fetchImpl } = {}) {
  const make = () => ({
    textContent: "", innerHTML: "", value: "", disabled: false, className: "",
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    children: [], appendChild(c) { this.children.push(c); return c; },
    addEventListener() {}, removeEventListener() {}, focus() {}, closest() { return null; },
    setPointerCapture() {}, releasePointerCapture() {}, querySelector() { return null; },
  });
  const els = new Map();
  const audioSrcs = [];
  const context = {
    console,
    setTimeout, clearTimeout, setInterval, clearInterval,
    requestAnimationFrame: () => 0, cancelAnimationFrame() {},
    fetch: fetchImpl || (async () => ({ ok: true, json: async () => ({}) })),
    URL: { createObjectURL: () => "blob:stub", revokeObjectURL() {} },
    CustomEvent: function (type, init) { return { type, detail: init?.detail }; },
    Audio: function (src) { audioSrcs.push(src); return { src, play: async () => {}, addEventListener() {}, playbackRate: 1 }; },
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
  return { ...context.window, audioSrcs };
}

const { isDeadCapture } = loadAppModule();

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

test("reuses a live unmuted session mic stream", () => {
  const { shouldReacquireMicStream } = loadAppModule();
  const stream = {
    active: true,
    getAudioTracks: () => [{ readyState: "live", muted: false, enabled: true }],
  };
  assert.equal(shouldReacquireMicStream(stream), false);
});

test("reacquires missing, ended, or muted mic streams", () => {
  const { shouldReacquireMicStream } = loadAppModule();
  const ended = {
    active: true,
    getAudioTracks: () => [{ readyState: "ended", muted: false, enabled: true }],
  };
  const muted = {
    active: true,
    getAudioTracks: () => [{ readyState: "live", muted: true, enabled: true }],
  };
  const inactive = {
    active: false,
    getAudioTracks: () => [{ readyState: "live", muted: false, enabled: true }],
  };
  assert.equal(shouldReacquireMicStream(null), true);
  assert.equal(shouldReacquireMicStream(ended), true);
  assert.equal(shouldReacquireMicStream(muted), true);
  assert.equal(shouldReacquireMicStream(inactive), true);
});

test("loadBufferedAudio fetches network URLs (buffer before play)", async () => {
  const calls = [];
  const { loadBufferedAudio } = loadAppModule({
    fetchImpl: async (u) => { calls.push(u); return { ok: true, blob: async () => ({}) }; },
  });
  const a = await loadBufferedAudio("/audio/x.wav");
  assert.ok(calls.includes("/audio/x.wav")); // streamed into memory, not played live
  assert.equal(a.playbackRate, 1.5);
});

test("loadBufferedAudio passes data: URLs straight through (no fetch)", async () => {
  const calls = [];
  const { loadBufferedAudio } = loadAppModule({
    fetchImpl: async (u) => { calls.push(u); return { ok: true, blob: async () => ({}) }; },
  });
  const a = await loadBufferedAudio("data:audio/wav;base64,AAAA");
  assert.ok(!calls.some((u) => u.startsWith("data:")));
  assert.equal(a.src, "data:audio/wav;base64,AAAA");
});
