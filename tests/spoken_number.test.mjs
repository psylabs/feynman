import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

function loadOfflineModule() {
  const context = {
    console,
    navigator: { onLine: true },
    window: {
      addEventListener() {},
    },
  };
  vm.runInNewContext(readFileSync("web/offline.js", "utf8"), context);
  return context.window;
}

test("parseTypedAnswer handles digit input", () => {
  const { parseTypedAnswer } = loadOfflineModule();

  const result = parseTypedAnswer("42");
  assert.equal(result.value, 42);
  assert.equal(result.skipped, false);
  assert.equal(result.raw, "42");
});

test("parseTypedAnswer handles spoken 'forty two'", () => {
  const { parseTypedAnswer } = loadOfflineModule();

  const result = parseTypedAnswer("forty two");
  assert.equal(result.value, 42);
  assert.equal(result.skipped, false);
});

test("parseTypedAnswer handles hyphenated 'forty-two'", () => {
  const { parseTypedAnswer } = loadOfflineModule();

  const result = parseTypedAnswer("forty-two");
  assert.equal(result.value, 42);
  assert.equal(result.skipped, false);
});

test("parseTypedAnswer handles 'one hundred and five'", () => {
  const { parseTypedAnswer } = loadOfflineModule();

  const result = parseTypedAnswer("one hundred and five");
  assert.equal(result.value, 105);
  assert.equal(result.skipped, false);
});

test("parseTypedAnswer handles 'one thousand two hundred thirty four'", () => {
  const { parseTypedAnswer } = loadOfflineModule();

  const result = parseTypedAnswer("one thousand two hundred thirty four");
  assert.equal(result.value, 1234);
  assert.equal(result.skipped, false);
});

test("parseTypedAnswer handles 'seven point five'", () => {
  const { parseTypedAnswer } = loadOfflineModule();

  const result = parseTypedAnswer("seven point five");
  assert.equal(result.value, 7.5);
  assert.equal(result.skipped, false);
});

test("parseTypedAnswer handles 'negative eight'", () => {
  const { parseTypedAnswer } = loadOfflineModule();

  const result = parseTypedAnswer("negative eight");
  assert.equal(result.value, -8);
  assert.equal(result.skipped, false);
});

test("parseTypedAnswer handles 'a hundred'", () => {
  const { parseTypedAnswer } = loadOfflineModule();

  const result = parseTypedAnswer("a hundred");
  assert.equal(result.value, 100);
  assert.equal(result.skipped, false);
});

test("parseTypedAnswer handles 'skip' as skipped", () => {
  const { parseTypedAnswer } = loadOfflineModule();

  const result = parseTypedAnswer("skip");
  assert.equal(result.value, null);
  assert.equal(result.skipped, true);
});

test("parseTypedAnswer handles invalid word 'banana' as null", () => {
  const { parseTypedAnswer } = loadOfflineModule();

  const result = parseTypedAnswer("banana");
  assert.equal(result.value, null);
  assert.equal(result.skipped, false);
});

test("parseTypedAnswer strips trailing period from 'forty two.'", () => {
  const { parseTypedAnswer } = loadOfflineModule();

  const result = parseTypedAnswer("forty two.");
  assert.equal(result.value, 42);
  assert.equal(result.skipped, false);
});

test("parseTypedAnswer strips trailing question mark from 'seven point five?'", () => {
  const { parseTypedAnswer } = loadOfflineModule();

  const result = parseTypedAnswer("seven point five?");
  assert.equal(result.value, 7.5);
  assert.equal(result.skipped, false);
});

test("parseTypedAnswer recognizes dictated 'skip.' with trailing period", () => {
  const { parseTypedAnswer } = loadOfflineModule();

  const result = parseTypedAnswer("skip.");
  assert.equal(result.value, null);
  assert.equal(result.skipped, true);
});
