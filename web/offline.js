// web/offline.js - native SQLite cache + outbox for offline drill sessions.
(function () {
  "use strict";

  var DB_NAME = "feynman_offline";
  var DB_VERSION = 1;
  var DEFAULT_SEED_COUNT = 50;

  var SCHEMA = `
    CREATE TABLE IF NOT EXISTS seed_pack (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id TEXT NOT NULL,
      skill_id TEXT NOT NULL,
      level INTEGER,
      prompt_text TEXT NOT NULL,
      expected_answer REAL NOT NULL,
      parameters_json TEXT NOT NULL,
      tolerance_json TEXT NOT NULL,
      audio_url TEXT,
      audio_data_url TEXT,
      audio_duration_ms INTEGER,
      position INTEGER NOT NULL,
      created_at TEXT NOT NULL,
      used_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_seed_pack_next
      ON seed_pack(user_id, used_at, id);

    CREATE TABLE IF NOT EXISTS attempts_outbox (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id TEXT NOT NULL,
      skill_id TEXT NOT NULL,
      prompt_text TEXT NOT NULL,
      expected_answer REAL NOT NULL,
      parsed_answer REAL,
      raw_transcript TEXT NOT NULL,
      skipped INTEGER NOT NULL DEFAULT 0,
      correct INTEGER NOT NULL DEFAULT 0,
      error_magnitude REAL,
      parameters_json TEXT NOT NULL,
      audio_duration_ms INTEGER,
      onset_latency_ms INTEGER,
      resolution_latency_ms INTEGER,
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_attempts_outbox_user
      ON attempts_outbox(user_id, id);
  `;

  function isoNow() {
    return new Date().toISOString();
  }

  function safeJsonParse(raw, fallback) {
    if (raw == null || raw === "") return fallback;
    if (typeof raw === "object") return raw;
    try { return JSON.parse(raw); } catch { return fallback; }
  }

  function boolish(value) {
    return value === true || value === 1 || value === "1";
  }

  function numberOrNull(value) {
    if (value == null || value === "") return null;
    var n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function latencyMs(start, end) {
    if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
    return Math.max(0, Math.round((end - start) * 1000));
  }

  function createCapacitorSQLiteDriver(database, plugin) {
    plugin = plugin || window.Capacitor?.Plugins?.CapacitorSQLite || window.CapacitorSQLite;
    if (!plugin) return null;

    return {
      async initConnection() {
        if (typeof plugin.createConnection === "function") {
          try {
            await plugin.createConnection({
              database,
              version: DB_VERSION,
              encrypted: false,
              mode: "no-encryption",
              readonly: false,
            });
          } catch (e) {
            if (!/already|exist|connection/i.test(String(e && (e.message || e)))) throw e;
          }
        }
        if (typeof plugin.open === "function") {
          await plugin.open({ database, readonly: false });
        }
      },
      execute(statements) {
        return plugin.execute({ database, statements, transaction: true });
      },
      run(statement, values) {
        return plugin.run({ database, statement, values: values || [], transaction: true });
      },
      query(statement, values) {
        return plugin.query({ database, statement, values: values || [], readonly: false });
      },
    };
  }

  function normalizeSeedRow(row) {
    if (!row) return null;
    return {
      local_id: row.id,
      user_id: row.user_id,
      skill_id: row.skill_id,
      level: row.level,
      prompt_text: row.prompt_text,
      expected: numberOrNull(row.expected_answer),
      parameters: safeJsonParse(row.parameters_json, {}),
      tolerance_rule: safeJsonParse(row.tolerance_json, {}),
      audio_url: row.audio_url || "",
      audio_data_url: row.audio_data_url || "",
      audio_duration_ms: row.audio_duration_ms == null ? null : Number(row.audio_duration_ms),
    };
  }

  function normalizeOutboxRow(row) {
    return {
      outbox_id: row.id,
      skill_id: row.skill_id,
      prompt_text: row.prompt_text,
      expected_answer: numberOrNull(row.expected_answer),
      parsed_answer: numberOrNull(row.parsed_answer),
      raw_transcript: row.raw_transcript || "",
      skipped: boolish(row.skipped),
      correct: boolish(row.correct),
      error_magnitude: numberOrNull(row.error_magnitude),
      parameters: safeJsonParse(row.parameters_json, {}),
      audio_duration_ms: row.audio_duration_ms == null ? null : Number(row.audio_duration_ms),
      onset_latency_ms: row.onset_latency_ms == null ? null : Number(row.onset_latency_ms),
      resolution_latency_ms: row.resolution_latency_ms == null ? null : Number(row.resolution_latency_ms),
    };
  }

  function createFeynmanOfflineStore(options) {
    options = options || {};
    var driver = options.driver || createCapacitorSQLiteDriver(options.database || DB_NAME, options.plugin);
    var now = options.now || isoNow;
    var initialized = false;

    async function ensureAvailable() {
      if (!driver) throw new Error("offline SQLite storage unavailable");
      if (initialized) return;
      if (typeof driver.initConnection === "function") await driver.initConnection();
      await driver.execute(SCHEMA);
      try {
        await driver.run("ALTER TABLE seed_pack ADD COLUMN audio_data_url TEXT", []);
      } catch {}
      initialized = true;
    }

    return {
      isAvailable: function () { return !!driver; },

      async init() {
        if (!driver) return false;
        await ensureAvailable();
        return true;
      },

      async saveSeedPack(pack) {
        await ensureAvailable();
        var userId = pack && pack.user_id;
        var items = (pack && pack.items) || [];
        if (!userId) throw new Error("seed pack user_id required");
        await driver.run("DELETE FROM seed_pack WHERE user_id = ?", [userId]);
        for (var i = 0; i < items.length; i++) {
          var item = items[i];
          await driver.run(
            `INSERT INTO seed_pack (
              user_id, skill_id, level, prompt_text, expected_answer,
              parameters_json, tolerance_json, audio_url, audio_data_url, audio_duration_ms,
              position, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
            [
              userId,
              item.skill_id,
              item.level == null ? null : Number(item.level),
              item.prompt_text,
              Number(item.expected),
              JSON.stringify(item.parameters || {}),
              JSON.stringify(item.tolerance_rule || {}),
              item.audio_url || "",
              item.audio_data_url || "",
              item.audio_duration_ms == null ? null : Number(item.audio_duration_ms),
              i + 1,
              now(),
            ]
          );
        }
        return items.length;
      },

      async nextSeed(userId) {
        await ensureAvailable();
        var res = await driver.query(
          `SELECT id, user_id, skill_id, level, prompt_text, expected_answer,
                  parameters_json, tolerance_json, audio_url, audio_data_url, audio_duration_ms
             FROM seed_pack
            WHERE user_id = ? AND used_at IS NULL
            ORDER BY id
            LIMIT 1`,
          [userId]
        );
        return normalizeSeedRow((res.values || [])[0]);
      },

      async markSeedUsed(localId) {
        await ensureAvailable();
        await driver.run("UPDATE seed_pack SET used_at = ? WHERE id = ?", [now(), localId]);
      },

      async queueAttempt(userId, attempt) {
        await ensureAvailable();
        var res = await driver.run(
          `INSERT INTO attempts_outbox (
            user_id, skill_id, prompt_text, expected_answer, parsed_answer,
            raw_transcript, skipped, correct, error_magnitude, parameters_json,
            audio_duration_ms, onset_latency_ms, resolution_latency_ms, created_at
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
          [
            userId,
            attempt.skill_id,
            attempt.prompt_text,
            Number(attempt.expected_answer),
            attempt.parsed_answer == null ? null : Number(attempt.parsed_answer),
            attempt.raw_transcript || "",
            attempt.skipped ? 1 : 0,
            attempt.correct ? 1 : 0,
            attempt.error_magnitude == null ? null : Number(attempt.error_magnitude),
            JSON.stringify(attempt.parameters || {}),
            attempt.audio_duration_ms == null ? null : Number(attempt.audio_duration_ms),
            attempt.onset_latency_ms == null ? null : Number(attempt.onset_latency_ms),
            attempt.resolution_latency_ms == null ? null : Number(attempt.resolution_latency_ms),
            now(),
          ]
        );
        return res?.changes?.lastId || 0;
      },

      async pendingOutbox(userId) {
        await ensureAvailable();
        var res = await driver.query(
          `SELECT id, skill_id, prompt_text, expected_answer, parsed_answer,
                  raw_transcript, skipped, correct, error_magnitude, parameters_json,
                  audio_duration_ms, onset_latency_ms, resolution_latency_ms
             FROM attempts_outbox
            WHERE user_id = ?
            ORDER BY id`,
          [userId]
        );
        return (res.values || []).map(normalizeOutboxRow);
      },

      async markOutboxSynced(ids) {
        await ensureAvailable();
        for (var i = 0; i < ids.length; i++) {
          await driver.run("DELETE FROM attempts_outbox WHERE id = ?", [ids[i]]);
        }
      },

      async stats(userId) {
        if (!driver) return { seedRemaining: 0, outboxPending: 0 };
        await ensureAvailable();
        var seed = await driver.query(
          "SELECT COUNT(*) AS count FROM seed_pack WHERE user_id = ? AND used_at IS NULL",
          [userId]
        );
        var outbox = await driver.query(
          "SELECT COUNT(*) AS count FROM attempts_outbox WHERE user_id = ?",
          [userId]
        );
        return {
          seedRemaining: Number((seed.values || [])[0]?.count || 0),
          outboxPending: Number((outbox.values || [])[0]?.count || 0),
        };
      },
    };
  }

  function parseTypedAnswer(raw) {
    raw = (raw || "").trim();
    if (!raw) return { value: null, skipped: false, raw: raw };
    var cleaned = raw.toLowerCase();
    if (/^(skip|pass|don'?t know|no idea|not sure|dunno)$/.test(cleaned)) {
      return { value: null, skipped: true, raw: raw };
    }
    cleaned = cleaned.replace(/[$,%]/g, "").replace(/,/g, "");
    var match = cleaned.match(/-?\d+(?:\.\d+)?/);
    if (!match) return { value: null, skipped: false, raw: raw };
    var n = Number(match[0]);
    return { value: Number.isFinite(n) ? n : null, skipped: false, raw: raw };
  }

  function buildOfflineAttempt(seed, rawAnswer, timing) {
    timing = timing || {};
    var parsed = parseTypedAnswer(rawAnswer);
    var expected = numberOrNull(seed.expected_answer ?? seed.expected);
    var tolerance = seed.tolerance_rule || seed.tolerance || {};
    var verdict = parsed.skipped
      ? { correct: false, error_magnitude: null, rule: "skipped" }
      : window.gradeAnswer(parsed.value, expected, tolerance);
    var onsetLatency = latencyMs(timing.promptEndTs, timing.onsetTs);
    var resolutionLatency = latencyMs(timing.promptEndTs, timing.resolutionTs);
    var attempt = {
      skill_id: seed.skill_id,
      prompt_text: seed.prompt_text,
      expected_answer: expected,
      parsed_answer: parsed.value,
      raw_transcript: parsed.raw,
      skipped: parsed.skipped,
      correct: verdict.correct,
      error_magnitude: verdict.error_magnitude,
      parameters: seed.parameters || {},
      audio_duration_ms: seed.audio_duration_ms == null ? null : Number(seed.audio_duration_ms),
      onset_latency_ms: onsetLatency,
      resolution_latency_ms: resolutionLatency,
    };
    return {
      attempt: attempt,
      result: {
        skipped: attempt.skipped,
        correct: attempt.correct,
        parsed: attempt.parsed_answer,
        expected: attempt.expected_answer,
        transcript: attempt.raw_transcript,
        resolution_latency_ms: attempt.resolution_latency_ms,
        feedback_pending: false,
        rule: verdict.rule,
      },
    };
  }

  function defaultBlobToDataUrl(blob) {
    if (typeof FileReader === "undefined") return Promise.resolve("");
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(String(reader.result || "")); };
      reader.onerror = function () { reject(reader.error || new Error("audio read failed")); };
      reader.readAsDataURL(blob);
    });
  }

  async function attachAudioData(pack, fetchFn, blobToDataUrl) {
    var items = (pack && pack.items) || [];
    for (var i = 0; i < items.length; i++) {
      var item = items[i];
      if (!item.audio_url || item.audio_data_url) continue;
      try {
        var res = await fetchFn(item.audio_url, { cache: "force-cache" });
        if (!res.ok) continue;
        var dataUrl = await blobToDataUrl(await res.blob());
        if (dataUrl) item.audio_data_url = dataUrl;
      } catch {}
    }
    return pack;
  }

  function getNetworkPlugin() {
    return window.Capacitor?.Plugins?.Network || window.Network || null;
  }

  async function onlineStatus() {
    var network = getNetworkPlugin();
    if (network && typeof network.getStatus === "function") {
      try {
        var status = await network.getStatus();
        return !!status.connected;
      } catch {}
    }
    return typeof navigator === "undefined" || navigator.onLine !== false;
  }

  function onOnline(callback) {
    var network = getNetworkPlugin();
    if (network && typeof network.addListener === "function") {
      network.addListener("networkStatusChange", function (status) {
        if (status.connected) callback();
      }).catch(function () {});
    }
    window.addEventListener("online", callback);
  }

  function createFeynmanOfflineController(options) {
    options = options || {};
    var store = options.store || createFeynmanOfflineStore(options);
    var fetchFn = options.fetch || (window.fetch ? window.fetch.bind(window) : null);
    var blobToDataUrl = options.blobToDataUrl || defaultBlobToDataUrl;

    async function init() {
      return store.init();
    }

    async function refreshSeedPack(userId, count) {
      if (!userId || !(await onlineStatus())) return { ok: false, reason: "offline" };
      await init();
      if (!fetchFn) throw new Error("fetch unavailable");
      var n = count || DEFAULT_SEED_COUNT;
      var res = await fetchFn("/seed-pack/" + encodeURIComponent(userId) + "?n=" + encodeURIComponent(n), {
        cache: "no-store",
      });
      if (!res.ok) throw new Error("seed pack HTTP " + res.status);
      var pack = await res.json();
      await attachAudioData(pack, fetchFn, blobToDataUrl);
      var saved = await store.saveSeedPack(pack);
      return { ok: true, count: saved };
    }

    async function flushOutbox(userId) {
      if (!userId || !(await onlineStatus())) return { ok: false, reason: "offline" };
      await init();
      if (!fetchFn) throw new Error("fetch unavailable");
      var pending = await store.pendingOutbox(userId);
      if (!pending.length) return { ok: true, synced: 0 };
      var attempts = pending.map(function (row) {
        var copy = Object.assign({}, row);
        delete copy.outbox_id;
        return copy;
      });
      var res = await fetchFn("/session/attempts/bulk", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId, attempts: attempts }),
      });
      if (!res.ok) throw new Error("bulk sync HTTP " + res.status);
      var body = await res.json();
      await store.markOutboxSynced(pending.map(function (row) { return row.outbox_id; }));
      return { ok: true, synced: body.synced || 0, response: body };
    }

    return {
      store: store,
      init: init,
      onlineStatus: onlineStatus,
      onOnline: onOnline,
      refreshSeedPack: refreshSeedPack,
      flushOutbox: flushOutbox,
      stats: function (userId) { return store.stats(userId); },
      nextSeed: function (userId) { return store.nextSeed(userId); },
      markSeedUsed: function (localId) { return store.markSeedUsed(localId); },
      queueAttempt: function (userId, attempt) { return store.queueAttempt(userId, attempt); },
      isAvailable: function () { return store.isAvailable(); },
    };
  }

  window.createFeynmanOfflineStore = createFeynmanOfflineStore;
  window.createFeynmanOfflineController = createFeynmanOfflineController;
  window.parseTypedAnswer = parseTypedAnswer;
  window.buildOfflineAttempt = buildOfflineAttempt;
  window.feynmanOffline = createFeynmanOfflineController();
})();
