import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import { DatabaseSync } from "node:sqlite";

class NodeSqliteDriver {
  constructor() {
    this.db = new DatabaseSync(":memory:");
  }

  async execute(statements) {
    this.db.exec(statements);
    return { changes: { changes: 0 } };
  }

  async run(statement, values = []) {
    const result = this.db.prepare(statement).run(...values);
    return {
      changes: {
        changes: Number(result.changes || 0),
        lastId: Number(result.lastInsertRowid || 0),
      },
    };
  }

  async query(statement, values = []) {
    return { values: this.db.prepare(statement).all(...values) };
  }
}

function loadOfflineModule() {
  const context = {
    console,
    navigator: { onLine: true },
    window: {
      addEventListener() {},
      gradeAnswer(parsed, expected, tolerance) {
        if (parsed == null || Number.isNaN(parsed)) {
          return { correct: false, error_magnitude: null, rule: "no_answer" };
        }
        if ((tolerance?.type || "exact") === "absolute") {
          const err = parsed - expected;
          const bound = Number(tolerance.value);
          return { correct: Math.abs(err) <= bound, error_magnitude: err, rule: `abs<=${bound}` };
        }
        return { correct: parsed === expected, error_magnitude: parsed - expected, rule: "exact" };
      },
    },
  };
  vm.runInNewContext(readFileSync("web/offline.js", "utf8"), context);
  return context.window;
}

function samplePack() {
  return {
    user_id: "u1",
    count: 2,
    items: [
      {
        skill_id: "add",
        level: 1,
        prompt_text: "What is 2 plus 2?",
        expected: 4,
        parameters: { a: 2, b: 2 },
        tolerance_rule: { type: "exact" },
        audio_url: "/audio/add.wav",
        audio_data_url: "data:audio/wav;base64,AAAA",
        audio_duration_ms: 900,
      },
      {
        skill_id: "money",
        level: 2,
        prompt_text: "What is $10 plus $2?",
        expected: 12,
        parameters: { a: 10, b: 2 },
        tolerance_rule: { type: "absolute", value: 0.01 },
        audio_url: "/audio/money.wav",
        audio_duration_ms: 1100,
      },
    ],
  };
}

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

test("seed packs are stored in SQLite and dequeued once", async () => {
  const { createFeynmanOfflineStore } = loadOfflineModule();
  const store = createFeynmanOfflineStore({ driver: new NodeSqliteDriver(), now: () => "2026-06-19T10:00:00.000Z" });

  await store.init();
  await store.saveSeedPack(samplePack());

  assert.deepEqual(plain(await store.stats("u1")), { seedRemaining: 2, outboxPending: 0 });

  const first = await store.nextSeed("u1");
  assert.equal(first.skill_id, "add");
  assert.equal(first.expected, 4);
  assert.deepEqual(plain(first.parameters), { a: 2, b: 2 });
  assert.deepEqual(plain(first.tolerance_rule), { type: "exact" });
  assert.equal(first.audio_data_url, "data:audio/wav;base64,AAAA");

  await store.markSeedUsed(first.local_id);
  const second = await store.nextSeed("u1");
  assert.equal(second.skill_id, "money");
  assert.deepEqual(plain(await store.stats("u1")), { seedRemaining: 1, outboxPending: 0 });
});

test("offline attempts are queued as bulk-sync payloads and removed after sync", async () => {
  const { createFeynmanOfflineStore } = loadOfflineModule();
  const store = createFeynmanOfflineStore({ driver: new NodeSqliteDriver(), now: () => "2026-06-19T10:00:00.000Z" });
  await store.init();
  await store.saveSeedPack(samplePack());

  const seed = await store.nextSeed("u1");
  const queuedId = await store.queueAttempt("u1", {
    skill_id: seed.skill_id,
    prompt_text: seed.prompt_text,
    expected_answer: seed.expected,
    parsed_answer: 4,
    raw_transcript: "4",
    skipped: false,
    correct: true,
    error_magnitude: 0,
    parameters: seed.parameters,
    audio_duration_ms: seed.audio_duration_ms,
    onset_latency_ms: 0,
    resolution_latency_ms: 1250,
  });

  const pending = await store.pendingOutbox("u1");
  assert.equal(pending.length, 1);
  assert.equal(pending[0].outbox_id, queuedId);
  assert.deepEqual(plain(pending[0]), {
    outbox_id: queuedId,
    skill_id: "add",
    prompt_text: "What is 2 plus 2?",
    expected_answer: 4,
    parsed_answer: 4,
    raw_transcript: "4",
    skipped: false,
    correct: true,
    error_magnitude: 0,
    parameters: { a: 2, b: 2 },
    audio_duration_ms: 900,
    onset_latency_ms: 0,
    resolution_latency_ms: 1250,
  });

  assert.deepEqual(plain(await store.stats("u1")), { seedRemaining: 2, outboxPending: 1 });
  await store.markOutboxSynced([queuedId]);
  assert.deepEqual(plain(await store.pendingOutbox("u1")), []);
});

test("typed offline answers build the same attempt shape the server bulk endpoint accepts", () => {
  const { buildOfflineAttempt } = loadOfflineModule();
  const seed = samplePack().items[0];

  const built = buildOfflineAttempt(seed, "4", {
    promptEndTs: 100,
    onsetTs: 100.2,
    resolutionTs: 101.75,
  });

  assert.equal(built.result.correct, true);
  assert.equal(built.result.parsed, 4);
  assert.equal(built.attempt.skill_id, "add");
  assert.equal(built.attempt.expected_answer, 4);
  assert.equal(built.attempt.parsed_answer, 4);
  assert.equal(built.attempt.skipped, false);
  assert.equal(built.attempt.onset_latency_ms, 200);
  assert.equal(built.attempt.resolution_latency_ms, 1750);
});

test("typed skip queues a skipped attempt without grading a numeric answer", () => {
  const { buildOfflineAttempt } = loadOfflineModule();
  const built = buildOfflineAttempt(samplePack().items[0], "skip", {
    promptEndTs: 100,
    onsetTs: 100,
    resolutionTs: 101,
  });

  assert.equal(built.result.skipped, true);
  assert.equal(built.attempt.parsed_answer, null);
  assert.equal(built.attempt.skipped, true);
  assert.equal(built.attempt.correct, false);
});

test("seed refresh stores prompt audio data for airplane mode playback", async () => {
  const { createFeynmanOfflineController } = loadOfflineModule();
  let savedPack = null;
  const calls = [];
  const pack = samplePack();
  delete pack.items[0].audio_data_url;
  delete pack.items[1].audio_data_url;

  const controller = createFeynmanOfflineController({
    store: {
      init: async () => true,
      saveSeedPack: async (nextPack) => {
        savedPack = nextPack;
        return nextPack.items.length;
      },
      isAvailable: () => true,
    },
    fetch: async (url) => {
      calls.push(url);
      if (String(url).startsWith("/seed-pack/")) {
        return { ok: true, json: async () => pack };
      }
      return { ok: true, blob: async () => ({ url }) };
    },
    blobToDataUrl: async (blob) => `data:audio/mock;base64,${String(blob.url).split("/").pop()}`,
  });

  const result = await controller.refreshSeedPack("u1", 2);

  assert.deepEqual(calls, ["/seed-pack/u1?n=2", "/audio/add.wav", "/audio/money.wav"]);
  assert.deepEqual(plain(result), { ok: true, count: 2 });
  assert.equal(savedPack.items[0].audio_data_url, "data:audio/mock;base64,add.wav");
  assert.equal(savedPack.items[1].audio_data_url, "data:audio/mock;base64,money.wav");
});
