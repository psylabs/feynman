// Main session controller.
(function () {
  const $ = (id) => document.getElementById(id);

  let sessionId = null;
  let currentQid = null;
  let currentAttemptId = null;
  let currentMode = "drill";
  let promptEndTs = null;
  let onsetTs = null;
  let mediaRecorder = null;
  let audioChunks = [];
  let stream = null;
  let recording = false;
  // Live input-level metering + energy capture (for calibrating a "bad clip"
  // retry threshold). audioCtx/analyser are torn down after each recording.
  let audioCtx = null;
  let analyser = null;
  let meterRaf = null;
  let energyFrames = [];
  const METER_SEGMENTS = 24;
  let advancing = false;
  let advanceTimer = null;
  let lastResult = null;
  let currentSessionPlan = null;
  let sessionPosition = 0;
  let sessionTargetQuestions = 0;
  let offlineSession = false;
  let offlineUserId = null;
  let offlineQuestion = null;
  let offlinePosition = 0;
  let offlineTarget = 0;
  let offlineReviewAttempts = [];
  let offlineSyncInFlight = false;
  let typedOnsetTs = null;

  // ---- screen switching --------------------------------------------------

  async function showScreen(id) {
    // Leaving an active session via nav: end it cleanly.
    if (sessionId && id !== "screen-session" && id !== "screen-review") {
      if (!offlineSession) {
        try {
          await fetch("/session/end", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: sessionId }),
          });
        } catch {}
      }
      sessionId = null;
      resetOfflineSession();
    }

    document.querySelectorAll(".screen").forEach((el) => el.classList.add("hidden"));
    $(id).classList.remove("hidden");
    document.querySelectorAll(".navlink").forEach((b) => {
      b.classList.toggle("active", b.dataset.screen === id);
    });

    if (id === "screen-profile") loadProfile();
    if (id === "screen-leaderboard") loadLeaderboard();
    if (id === "screen-start") refreshStartScreen();
    if (id === "screen-settings") loadSettings();
  }

  // ---- settings ----------------------------------------------------------

  function loadSettings() {
    const input = $("setting-api-base");
    if (input) input.value = window.feynmanSettings?.getApiBase?.() || "";
    $("settings-status").textContent = window.feynmanSettings?.isBundled
      ? ""
      : "You're in a browser — this only affects the installed app.";
    updateNotificationsStatus();
  }

  function updateNotificationsStatus(message) {
    const el = $("notifications-status");
    const enable = $("btn-notifications-enable");
    const disable = $("btn-notifications-disable");
    const reminders = window.feynmanNotifications;
    if (!el) return;
    if (!reminders) {
      el.textContent = "Notifications unavailable.";
      if (enable) enable.disabled = true;
      if (disable) disable.disabled = true;
      return;
    }

    const supported = reminders.isSupported();
    if (enable) enable.disabled = !supported;
    if (disable) disable.disabled = !supported || !reminders.isEnabled();
    if (message) {
      el.textContent = message;
    } else if (!supported) {
      el.textContent = window.feynmanSettings?.isBundled
        ? "Notifications unavailable on this build."
        : "Reminders are available in the installed app.";
    } else if (reminders.isEnabled()) {
      el.textContent = `Reminders on: ${reminders.summary()}`;
    } else {
      el.textContent = "Reminders off.";
    }
  }

  async function enableNotificationReminders() {
    const reminders = window.feynmanNotifications;
    if (!reminders) return;
    updateNotificationsStatus("Enabling reminders...");
    try {
      const result = await reminders.enable();
      updateNotificationsStatus(result.enabled ? `Reminders on: ${result.summary}` : "Notifications unavailable.");
    } catch (e) {
      updateNotificationsStatus(`Notifications failed: ${e.message || e}`);
    }
  }

  async function disableNotificationReminders() {
    const reminders = window.feynmanNotifications;
    if (!reminders) return;
    updateNotificationsStatus("Turning off reminders...");
    try {
      await reminders.disable();
      updateNotificationsStatus("Reminders off.");
    } catch (e) {
      updateNotificationsStatus(`Notifications failed: ${e.message || e}`);
    }
  }

  function resetOfflineSession() {
    offlineSession = false;
    offlineUserId = null;
    offlineQuestion = null;
    offlinePosition = 0;
    offlineTarget = 0;
    offlineReviewAttempts = [];
    typedOnsetTs = null;
  }

  function offlineAvailable() {
    return !!window.feynmanOffline?.isAvailable?.();
  }

  async function updateOfflineStatus(userId) {
    const el = $("offline-status");
    const syncEl = $("sync-status");
    if (!el) return;
    if (!userId || !offlineAvailable()) {
      el.classList.add("hidden");
      if (syncEl) syncEl.classList.add("hidden");
      return;
    }
    try {
      await window.feynmanOffline.init();
      const s = await window.feynmanOffline.stats(userId);
      el.textContent = `${s.seedRemaining} offline prompts · ${s.outboxPending} pending`;
      el.classList.toggle("pending", s.outboxPending > 0);
      el.classList.remove("hidden");
      if (syncEl) {
        const last = s.lastSync || {};
        syncEl.classList.remove("synced", "error");
        if (last.last_success_at && last.last_summary_text) {
          const when = new Date(last.last_success_at).toLocaleString([], {
            month: "short",
            day: "numeric",
            hour: "numeric",
            minute: "2-digit",
          });
          syncEl.textContent = `Last sync: ${last.last_summary_text} · ${when}`;
          syncEl.classList.add("synced");
          syncEl.classList.remove("hidden");
        } else if (last.last_error_at && last.last_error) {
          syncEl.textContent = `Sync waiting: ${last.last_error}`;
          syncEl.classList.add("error");
          syncEl.classList.remove("hidden");
        } else {
          syncEl.classList.add("hidden");
        }
      }
    } catch {
      el.classList.add("hidden");
      if (syncEl) syncEl.classList.add("hidden");
    }
  }

  async function backgroundOfflineSync(userId) {
    if (!userId || !offlineAvailable() || offlineSyncInFlight) return;
    offlineSyncInFlight = true;
    try {
      await window.feynmanOffline.init();
      await window.feynmanOffline.flushOutbox(userId);
      const s = await window.feynmanOffline.stats(userId);
      if (s.seedRemaining < 4) await window.feynmanOffline.refreshSeedPack(userId, 10);
    } catch (e) {
      // Offline or backend unreachable. The visible status is refreshed below.
    } finally {
      offlineSyncInFlight = false;
      updateOfflineStatus(userId);
    }
  }

  // ---- start screen state ------------------------------------------------

  function refreshStartScreen() {
    const user = window.feynmanUser?.getCurrent?.();
    $("greeting").textContent = user ? `Hi, ${user.name}.` : "Hi.";
    $("diagnosis-teaser").classList.add("hidden");
    $("home-stats").classList.add("hidden");
    loadVersionIndicator();
    if (!user) {
      updateOfflineStatus(null);
      return;
    }
    if (user.has_completed_eval) {
      $("eval-banner").classList.add("hidden");
    } else {
      $("eval-banner").classList.remove("hidden");
    }
    loadDiagnosisTeaser(user.id);
    updateOfflineStatus(user.id);
    backgroundOfflineSync(user.id);
  }

  async function loadVersionIndicator() {
    const el = $("version-indicator");
    if (!el) return;
    try {
      const v = window.feynmanVersion?.load
        ? await window.feynmanVersion.load()
        : await fetch("/version", { cache: "no-store" }).then((r) => r.json());
      if (!v || (!v.sha && !v.committed_at)) { el.classList.add("hidden"); return; }
      const when = v.committed_at ? fmtRelative(v.committed_at) : "";
      const sha = v.sha ? `<code>${escapeHtml(v.sha)}</code>` : "";
      const msg = v.message ? `<span class="vi-msg">${escapeHtml(v.message.slice(0, 100))}${v.message.length > 100 ? "…" : ""}</span>` : "";
      const label = v.source === "app" ? "App updated" : "Server updated";
      el.innerHTML = `${label} ${escapeHtml(when)} · ${sha}${msg ? " — " + msg : ""}`;
      el.classList.remove("hidden");
    } catch {
      el.classList.add("hidden");
    }
  }

  function fmtRelative(iso) {
    const t = Date.parse(iso);
    if (!Number.isFinite(t)) return iso;
    const diffSec = Math.max(0, (Date.now() - t) / 1000);
    if (diffSec < 60) return "just now";
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
    if (diffSec < 86400 * 7) return `${Math.floor(diffSec / 86400)}d ago`;
    return new Date(t).toLocaleDateString();
  }

  async function loadDiagnosisTeaser(userId) {
    const el = $("diagnosis-teaser");
    try {
      const d = await fetch(`/diagnosis/${userId}`).then((r) => r.json());
      renderHomeStats(d);
      el.innerHTML = renderTeaser(d);
      el.classList.remove("hidden");
    } catch {
      el.classList.add("hidden");
    }
  }

  function renderHomeStats(d) {
    const el = $("home-stats");
    const streak = d.streak_current ?? 0;
    const total = d.streak_total ?? 0;
    const fluency = d.fluency_ms;
    const delta = d.fluency_delta_ms;
    if (!total && !fluency) { el.classList.add("hidden"); return; }

    let streakHtml = `<div class="stat-block">
      <div class="stat-val">${streak > 0 ? `🔥 ${streak}` : total}</div>
      <div class="stat-label">${streak > 0 ? `day streak · ${total} total` : "days practiced"}</div>
    </div>`;

    let fluencyHtml = "";
    if (fluency) {
      const sec = (fluency / 1000).toFixed(1);
      let deltaHtml = "";
      if (delta != null) {
        const sign = delta < 0 ? "↓" : "↑";
        const cls = delta < 0 ? "good" : "bad";
        deltaHtml = `<span class="stat-delta ${cls}">${sign}${(Math.abs(delta)/1000).toFixed(1)}s</span>`;
      }
      fluencyHtml = `<div class="stat-block">
        <div class="stat-val">${sec}s${deltaHtml}</div>
        <div class="stat-label">median response · 14d</div>
      </div>`;
    }

    el.innerHTML = streakHtml + fluencyHtml;
    el.classList.remove("hidden");
  }

  function renderTeaser(d) {
    if (!d || !d.total_attempts) {
      return `<div class="teaser teaser-cold">
        <div class="teaser-head">No data yet.</div>
        <div class="teaser-sub">Take a baseline (or run a drill) and the system will start flagging your slowest facts here.</div>
      </div>`;
    }
    const conf = d.confidence || "low";
    if (conf === "low" || !d.focus?.length) {
      return `<div class="teaser teaser-cold">
        <div class="teaser-head">Diagnosis confidence: low</div>
        <div class="teaser-sub">Only ${d.total_attempts} attempts so far. Sessions will be exploratory until more data accumulates.</div>
      </div>`;
    }
    const focusBits = d.focus.map((f) => {
      return `<li><strong>${escapeHtml(f.display)}</strong> <span class="muted small">— ${fmtSec(f.median_latency_ms)} (target ${fmtSec(f.target_ms)}) · n=${f.n}</span> ${confChip(f.confidence)}</li>`;
    }).join("");
    let regBit = "";
    if (d.regression) {
      const r = d.regression;
      regBit = `<div class="teaser-reg">Regression: <strong>${escapeHtml(r.display)}</strong> went from ${fmtSec(r.old_median_ms)} to ${fmtSec(r.recent_median_ms)}.</div>`;
    }
    return `<div class="teaser">
      <div class="teaser-head">Your slowest right now</div>
      <ul class="teaser-list">${focusBits}</ul>
      ${regBit}
      <div class="teaser-foot">This drill will focus there. Confidence: ${conf} · ${d.total_attempts} attempts.</div>
    </div>`;
  }

  function fmtSec(ms) {
    if (ms == null) return "—";
    return `${(ms / 1000).toFixed(1)}s`;
  }

  function confChip(level) {
    const cls = level === "high" ? "chip-high" : level === "medium" ? "chip-mid" : "chip-low";
    return `<span class="conf-chip ${cls}">${level}</span>`;
  }

  function confidenceFor(n) {
    if (n == null || n < 5) return "low";
    if (n < 15) return "medium";
    return "high";
  }

  window.addEventListener("feynman:user-changed", () => {
    // If on a profile/leaderboard screen, re-render for the new user.
    const visible = document.querySelector(".screen:not(.hidden)");
    if (!visible) return;
    if (visible.id === "screen-profile") loadProfile();
    if (visible.id === "screen-leaderboard") loadLeaderboard();
    if (visible.id === "screen-start") refreshStartScreen();
  });

  // ---- session lifecycle -------------------------------------------------

  async function startSession(mode, fixedLength) {
    const user = window.feynmanUser?.getCurrent?.();
    if (!user) return alert("Pick a player first.");
    const body = { user_id: user.id, mode };
    let targetQuestions = null;
    if (mode === "drill") {
      const n = fixedLength ?? parseInt(($("drill-length") || {}).value, 10);
      if (Number.isFinite(n)) {
        body.target_questions = n;
        targetQuestions = n;
      }
      backgroundOfflineSync(user.id);
    }
    let r;
    try {
      r = await fetch("/session/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then((res) => res.json());
    } catch {
      if (mode === "drill") return startOfflineSession(user, targetQuestions || 5);
      return alert("Could not reach the backend.");
    }
    if (r.detail) return alert(r.detail);
    resetOfflineSession();
    sessionId = r.session_id;
    currentMode = r.mode;
    currentSessionPlan = r.session_plan || null;
    sessionPosition = 0;
    sessionTargetQuestions = targetQuestions || 0;
    document.querySelectorAll(".screen").forEach((el) => el.classList.add("hidden"));
    $("screen-session").classList.remove("hidden");
    $("mode-tag").textContent = currentMode === "eval" ? "Baseline" : "Drill";
    renderSessionPlan(currentSessionPlan);
    $("result").textContent = "";
    $("result").className = "";
    $("feedback").textContent = "";
    await nextQuestion();
  }

  async function startOfflineSession(user, targetQuestions) {
    if (!offlineAvailable()) return alert("Offline storage is not ready.");
    try {
      await window.feynmanOffline.init();
      const seed = await window.feynmanOffline.nextSeed(user.id);
      if (!seed) return alert("No offline prompts cached. Connect once, then try again.");
    } catch {
      return alert("Offline storage is not ready.");
    }

    resetOfflineSession();
    offlineSession = true;
    offlineUserId = user.id;
    offlineTarget = targetQuestions || 5;
    sessionPosition = 0;
    sessionTargetQuestions = offlineTarget;
    sessionId = `offline:${Date.now()}`;
    currentMode = "drill";
    currentSessionPlan = null;
    document.querySelectorAll(".screen").forEach((el) => el.classList.add("hidden"));
    $("screen-session").classList.remove("hidden");
    $("mode-tag").textContent = "Offline";
    renderSessionPlan(null);
    $("result").textContent = "";
    $("result").className = "";
    $("feedback").textContent = "";
    await nextQuestion();
  }

  async function nextQuestion() {
    if (offlineSession) return nextOfflineQuestion();

    advancing = false;
    if (advanceTimer) {
      clearTimeout(advanceTimer);
      advanceTimer = null;
    }
    $("result").textContent = "";
    $("result").className = "";
    $("feedback").textContent = "";
    $("btn-next").classList.add("hidden");
    $("status").textContent = "Loading…";
    $("btn-ptt").disabled = true;
    $("btn-skip-voice").disabled = true;
    $("prompt").textContent = "";

    let r;
    try {
      r = await fetch("/session/next", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId }),
      }).then((res) => res.json());
    } catch {
      if (currentMode === "drill") return continueOfflineSession();
      $("status").textContent = "Could not load the next question.";
      return;
    }

    currentQid = r.qid;
    sessionPosition = r.position || sessionPosition;
    sessionTargetQuestions = r.target_questions || sessionTargetQuestions;
    hideTypedAnswer();
    $("btn-ptt").classList.remove("hidden");
    $("btn-skip-voice").classList.remove("hidden");
    $("prompt").textContent = r.prompt_text;
    $("position").textContent = `Question ${r.position} of ${r.target_questions}`;

    $("status").textContent = "Listening…";
    let audio;
    try {
      audio = await loadBufferedAudio(apiUrl(r.audio_url));
    } catch {
      // Buffering failed — stream directly so we still attempt playback.
      audio = new Audio(apiUrl(r.audio_url));
      audio.playbackRate = 1.5;
    }
    audio.addEventListener("ended", () => {
      promptEndTs = Date.now() / 1000;
      $("status").textContent = "Hold to answer (or hold spacebar)";
      $("btn-ptt").disabled = false;
      $("btn-skip-voice").disabled = false;
    });
    try {
      await audio.play();
    } catch {
      promptEndTs = Date.now() / 1000;
      $("status").textContent = "Audio blocked — answer now";
      $("btn-ptt").disabled = false;
      $("btn-skip-voice").disabled = false;
    }
  }

  function hideTypedAnswer() {
    const form = $("typed-answer");
    if (!form) return;
    form.classList.add("hidden");
    $("typed-answer-input").value = "";
    $("typed-answer-input").disabled = true;
    $("btn-submit-typed").disabled = true;
    $("btn-skip-typed").disabled = true;
  }

  function showTypedAnswer(disabled) {
    const form = $("typed-answer");
    if (!form) return;
    form.classList.remove("hidden");
    $("typed-answer-input").disabled = !!disabled;
    $("btn-submit-typed").disabled = !!disabled;
    $("btn-skip-typed").disabled = !!disabled;
    if (!disabled) setTimeout(() => $("typed-answer-input").focus(), 0);
  }

  async function continueOfflineSession() {
    const user = window.feynmanUser?.getCurrent?.();
    if (!user || !offlineAvailable()) {
      $("status").textContent = "No offline prompts cached.";
      return;
    }
    try {
      await window.feynmanOffline.init();
      const seed = await window.feynmanOffline.nextSeed(user.id);
      if (!seed) {
        $("status").textContent = "No offline prompts cached.";
        return;
      }
    } catch {
      $("status").textContent = "Offline storage is not ready.";
      return;
    }
    offlineSession = true;
    offlineUserId = user.id;
    offlineTarget = sessionTargetQuestions || 5;
    offlinePosition = sessionPosition;
    offlineReviewAttempts = [];
    offlineQuestion = null;
    sessionId = `offline:${Date.now()}`;
    $("mode-tag").textContent = "Offline";
    renderSessionPlan(null);
    await nextOfflineQuestion();
  }

  async function nextOfflineQuestion() {
    advancing = false;
    if (advanceTimer) {
      clearTimeout(advanceTimer);
      advanceTimer = null;
    }
    if (offlinePosition >= offlineTarget) return endSession();

    $("result").textContent = "";
    $("result").className = "";
    $("feedback").textContent = "";
    $("btn-next").classList.add("hidden");
    $("btn-ptt").classList.add("hidden");
    $("btn-ptt").disabled = true;
    $("btn-skip-voice").classList.add("hidden");
    showTypedAnswer(true);
    $("status").textContent = "Loading...";
    $("prompt").textContent = "";

    let seed;
    try {
      seed = await window.feynmanOffline.nextSeed(offlineUserId);
    } catch {
      $("status").textContent = "Offline storage is not ready.";
      return;
    }
    if (!seed) return endSession();

    offlineQuestion = seed;
    currentQid = `offline:${seed.local_id}`;
    offlinePosition += 1;
    sessionPosition = offlinePosition;
    $("prompt").textContent = seed.prompt_text;
    $("position").textContent = `Question ${offlinePosition} of ${offlineTarget}`;
    typedOnsetTs = null;

    const audioSrc = seed.audio_data_url || apiUrl(seed.audio_url || "");
    const enable = (message) => {
      promptEndTs = Date.now() / 1000;
      $("status").textContent = message;
      showTypedAnswer(false);
    };

    if (!audioSrc) {
      enable("Type answer");
      return;
    }
    $("status").textContent = "Listening...";
    let audio;
    try {
      audio = await loadBufferedAudio(audioSrc);
    } catch {
      audio = new Audio(audioSrc);
      audio.playbackRate = 1.5;
    }
    audio.addEventListener("ended", () => enable("Type answer"));
    try {
      await audio.play();
    } catch {
      enable("Type answer");
    }
  }

  // Fully buffer prompt audio before playing. Streaming a WAV and calling
  // play() immediately underruns at 1.5x over the network — the clip cuts off
  // or garbles. Fetching to a blob first (like the offline data-URL path,
  // which never underran) guarantees the whole clip is in memory. The object
  // URL is revoked once playback ends. data: URLs are already in-memory, so
  // they bypass the fetch.
  async function loadBufferedAudio(src) {
    if (src && src.startsWith("data:")) {
      const a = new Audio(src);
      a.playbackRate = 1.5;
      return a;
    }
    const res = await fetch(src);
    if (!res.ok) throw new Error("audio HTTP " + res.status);
    const url = URL.createObjectURL(await res.blob());
    const a = new Audio(url);
    a.playbackRate = 1.5;
    a.addEventListener("ended", () => URL.revokeObjectURL(url), { once: true });
    a.addEventListener("error", () => URL.revokeObjectURL(url), { once: true });
    return a;
  }
  window.loadBufferedAudio = loadBufferedAudio;

  function buildMeter() {
    const meter = $("input-meter");
    if (!meter || meter.children.length) return;
    for (let i = 0; i < METER_SEGMENTS; i++) {
      const s = document.createElement("span");
      s.className = "seg";
      meter.appendChild(s);
    }
  }

  function renderMeter(rms) {
    const meter = $("input-meter");
    if (!meter) return;
    const segs = meter.children;
    const n = segs.length;
    // Map RMS to a -60..-10 dBFS fill so quiet speech still moves the meter.
    const db = 20 * Math.log10(rms + 1e-9);
    const fill = Math.max(0, Math.min(1, (db + 60) / 50));
    const lit = Math.round(fill * n);
    for (let i = 0; i < n; i++) {
      const on = i < lit;
      const tier = i >= n * 0.85 ? " hot" : i >= n * 0.6 ? " mid" : "";
      segs[i].className = "seg" + (on ? " on" + tier : "");
    }
  }

  function startMetering() {
    energyFrames = [];
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      audioCtx = new Ctx();
      const src = audioCtx.createMediaStreamSource(stream);
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 1024;
      src.connect(analyser);
    } catch {
      analyser = null;
      return; // metering is best-effort; recording proceeds without it
    }
    buildMeter();
    $("input-meter").classList.remove("hidden");
    const buf = new Float32Array(analyser.fftSize);
    const tick = () => {
      if (!analyser) return;
      analyser.getFloatTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      const rms = Math.sqrt(sum / buf.length);
      energyFrames.push(rms);
      renderMeter(rms);
      meterRaf = requestAnimationFrame(tick);
    };
    meterRaf = requestAnimationFrame(tick);
  }

  function stopMetering() {
    if (meterRaf) cancelAnimationFrame(meterRaf);
    meterRaf = null;
    analyser = null;
    if (audioCtx) { try { audioCtx.close(); } catch {} audioCtx = null; }
    const meter = $("input-meter");
    if (meter) meter.classList.add("hidden");
  }

  function energyMetrics(clipMs) {
    const f = energyFrames;
    const n = f.length;
    if (!n) return { frames: 0 };
    const sorted = [...f].sort((a, b) => a - b);
    const floor = sorted[Math.floor(n * 0.1)];
    const peak = sorted[n - 1];
    const mean = f.reduce((a, b) => a + b, 0) / n;
    // "Voiced" = frames clearly above this clip's own noise floor.
    const thr = Math.max(floor * 3, 0.01);
    const voiced = f.filter((r) => r > thr).length;
    const r4 = (x) => Math.round(x * 1e4) / 1e4;
    return {
      frames: n,
      peak_rms: r4(peak),
      mean_rms: r4(mean),
      floor_rms: r4(floor),
      voiced_ms: Math.round((voiced / n) * clipMs),
    };
  }

  // True when a clip clearly captured no microphone signal this session:
  // either a header-only WebM (~110 bytes) or recorded near-silence
  // (peak RMS below DEAD_PEAK_RMS). Conservative on purpose — when energy
  // wasn't measured (metering failed to init), we DON'T flag, to avoid
  // false-rejecting a real answer. Pure function for testability.
  const DEAD_BYTES = 200;
  const DEAD_PEAK_RMS = 0.01;
  function isDeadCapture(meta) {
    if (!meta) return false;
    if (typeof meta.bytes === "number" && meta.bytes <= DEAD_BYTES) return true;
    const e = meta.energy;
    if (e && e.frames > 0 && typeof e.peak_rms === "number") {
      return e.peak_rms < DEAD_PEAK_RMS;
    }
    return false;
  }
  window.isDeadCapture = isDeadCapture;

  async function startRecording() {
    if (recording || $("btn-ptt").disabled || !currentQid) return;

    // mediaDevices is undefined on plain HTTP on most mobile browsers.
    // The Chrome flag `unsafely-treat-insecure-origin-as-secure` makes
    // getUserMedia work but leaves isSecureContext false — so check the API
    // directly and let getUserMedia surface its own error.
    if (!navigator.mediaDevices?.getUserMedia) {
      $("status").textContent = "Microphone needs HTTPS. Use Tailscale or open this on the Mac.";
      return;
    }

    recording = true;
    onsetTs = Date.now() / 1000;
    audioChunks = [];
    $("btn-ptt").classList.add("recording");
    $("btn-ptt").textContent = "Recording…";

    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      const name = err && err.name;
      $("status").textContent =
        name === "NotAllowedError" ? "Microphone permission denied — allow it in browser settings."
        : name === "NotFoundError" ? "No microphone found."
        : (name === "SecurityError" || name === "NotSupportedError") ? "Microphone needs HTTPS. Use Tailscale or open this on the Mac."
        : `Microphone error: ${name || "unknown"}`;
      recording = false;
      $("btn-ptt").classList.remove("recording");
      $("btn-ptt").textContent = "Press & hold to answer";
      return;
    }

    startMetering();
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) audioChunks.push(e.data);
    };
    mediaRecorder.onstop = async () => {
      const resolutionTs = Date.now() / 1000;
      const clipMs = Math.round((resolutionTs - onsetTs) * 1000);
      const energy = energyMetrics(clipMs);
      stopMetering();
      try {
        if (stream) stream.getTracks().forEach((t) => t.stop());
      } catch {}
      const mime = mediaRecorder.mimeType || "audio/webm";
      const blob = new Blob(audioChunks, { type: mime });
      // Diagnostics: device model + WebView version (UA), actual codec the
      // device produced, payload size, recorded duration, chunk count, and
      // input-energy summary (for calibrating a bad-clip retry threshold).
      // Logged server-side so STT failures can be correlated with hardware.
      const clientMeta = {
        ua: navigator.userAgent,
        mime,
        bytes: blob.size,
        clip_ms: clipMs,
        chunks: audioChunks.length,
        energy,
      };
      // Dead-capture guard: when the mic track delivered no signal this
      // session (header-only ~110-byte clip, or recorded near-silence), don't
      // ship silence to STT — it just comes back empty/"skip" and confuses the
      // user. Re-prompt locally instead. Skip/typed remain available, so this
      // is never a dead end. (peak_rms<0.01 is ~4x below the quietest real
      // speech we've logged, so it won't false-reject a soft answer.)
      if (isDeadCapture(clientMeta)) {
        $("status").textContent = "Mic didn't catch that — try again.";
        $("btn-ptt").disabled = false;
        $("btn-skip-voice").disabled = false;
        return;
      }
      await submitAnswer(blob, resolutionTs, clientMeta);
    };
    // Timeslice: flush an audio chunk every 250ms instead of only at stop, so a
    // momentary encoder stall on Android WebView costs one chunk, not the whole
    // clip (the header-only 110-byte empties).
    mediaRecorder.start(250);
  }

  async function stopRecording() {
    if (!recording) return;
    recording = false;
    $("btn-ptt").classList.remove("recording");
    $("btn-ptt").textContent = "Press & hold to answer";
    $("btn-ptt").disabled = true;
    $("btn-skip-voice").disabled = true;
    $("status").textContent = "Transcribing…";
    if (mediaRecorder && mediaRecorder.state === "recording") mediaRecorder.stop();
  }

  async function submitVoiceSkip() {
    if (!currentQid || !sessionId || recording) return;
    if ($("btn-skip-voice").disabled) return;
    $("btn-ptt").disabled = true;
    $("btn-skip-voice").disabled = true;
    $("status").textContent = "Skipping…";
    const resolutionTs = Date.now() / 1000;
    const fd = new FormData();
    fd.append("session_id", sessionId);
    fd.append("qid", currentQid);
    fd.append("prompt_end_ts", String(promptEndTs));
    fd.append("onset_ts", String(onsetTs || promptEndTs || resolutionTs));
    fd.append("resolution_ts", String(resolutionTs));
    fd.append("manual_skip", "true");

    let r;
    try {
      r = await fetch("/session/submit", { method: "POST", body: fd }).then((res) => res.json());
    } catch {
      $("status").textContent = "Skip failed";
      $("btn-ptt").disabled = false;
      $("btn-skip-voice").disabled = false;
      return;
    }
    lastResult = r;
    currentAttemptId = r.attempt_id || null;
    renderAttemptResult(r);
    if (advancing) return;
    advancing = true;
    advanceTimer = setTimeout(advanceNow, 800);
  }

  async function submitAnswer(blob, resolutionTs, clientMeta) {
    const fd = new FormData();
    fd.append("session_id", sessionId);
    fd.append("qid", currentQid);
    fd.append("prompt_end_ts", String(promptEndTs));
    fd.append("onset_ts", String(onsetTs));
    fd.append("resolution_ts", String(resolutionTs));
    fd.append("audio", blob, "answer.webm");
    if (clientMeta) fd.append("client_meta", JSON.stringify(clientMeta));

    let r;
    try {
      r = await fetch("/session/submit", { method: "POST", body: fd }).then((res) =>
        res.json()
      );
    } catch {
      $("status").textContent = "Submit failed";
      return;
    }

    if (r.audio_failed) {
      $("status").textContent = r.message || "Didn't catch that — try again";
      $("result").textContent = "";
      $("result").className = "";
      $("feedback").textContent = "";
      $("btn-next").classList.add("hidden");
      $("btn-ptt").disabled = false;
      $("btn-skip-voice").disabled = false;
      return;
    }

    lastResult = r;
    currentAttemptId = r.attempt_id || null;

    renderAttemptResult(r);

    // Prefetch the next question while the user reads this result.
    // Fire-and-forget: failures fall back to the live /session/next path.
    if (sessionId && r.position < r.target_questions) {
      fetch("/session/peek", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId }),
      }).catch(() => {});
    }

    if (advancing) return;
    advancing = true;
    const delay = r.feedback_pending ? 5000 : (r.correct ? 600 : 2000);
    advanceTimer = setTimeout(advanceNow, delay);
  }

  function renderAttemptResult(r) {
    let label, cls;
    if (r.skipped) { label = "Skipped"; cls = "skipped"; }
    else if (r.correct) { label = "✓ Correct"; cls = "correct"; }
    else { label = "✗ Wrong"; cls = "wrong"; }
    const detail = r.skipped ? "" : `  ·  you said ${fmt(r.parsed)}, expected ${fmt(r.expected)}`;
    const timing = r.resolution_latency_ms != null ? `  ·  ${fmtSec(r.resolution_latency_ms)}` : "";
    $("result").textContent = `${label}${detail}${timing}`;
    $("result").className = cls;
    $("status").textContent = r.skipped ? "Skipped." : `“${r.transcript || ""}”`;
    $("feedback").textContent = r.feedback_pending ? "thinking…" : "";
    $("btn-next").classList.remove("hidden");
  }

  async function submitTypedAnswer(rawAnswer) {
    if (!offlineSession || !offlineQuestion) return;
    rawAnswer = (rawAnswer || "").trim();
    if (!rawAnswer) {
      $("typed-answer-input").focus();
      return;
    }

    showTypedAnswer(true);
    $("status").textContent = "Saving...";
    const resolutionTs = Date.now() / 1000;
    const onset = typedOnsetTs || promptEndTs || resolutionTs;
    const built = window.buildOfflineAttempt(offlineQuestion, rawAnswer, {
      promptEndTs: promptEndTs || onset,
      onsetTs: onset,
      resolutionTs,
    });

    try {
      built.attempt.local_session_id = sessionId;
      built.attempt.position_in_session = offlinePosition;
      const queued = await window.feynmanOffline.queueAttempt(offlineUserId, built.attempt);
      built.attempt.client_id = queued.clientId;
      await window.feynmanOffline.markSeedUsed(offlineQuestion.local_id);
    } catch {
      $("status").textContent = "Could not save offline answer.";
      showTypedAnswer(false);
      return;
    }

    const r = {
      ...built.result,
      position: offlinePosition,
      target_questions: offlineTarget,
    };
    lastResult = r;
    currentAttemptId = null;
    offlineReviewAttempts.push({
      id: null,
      client_id: built.attempt.client_id,
      local_session_id: sessionId,
      user_id: offlineUserId,
      position_in_session: offlinePosition,
      skill_id: offlineQuestion.skill_id,
      prompt_text: offlineQuestion.prompt_text,
      parsed_answer: built.attempt.parsed_answer,
      expected_answer: built.attempt.expected_answer,
      correct: built.attempt.correct,
      skipped: built.attempt.skipped,
      resolution_latency_ms: built.attempt.resolution_latency_ms,
      parameters: offlineQuestion.parameters || {},
    });
    offlineQuestion = null;
    hideTypedAnswer();
    renderAttemptResult(r);
    updateOfflineStatus(offlineUserId);
    backgroundOfflineSync(offlineUserId);

    if (advancing) return;
    advancing = true;
    const delay = r.correct ? 600 : 1800;
    advanceTimer = setTimeout(advanceNow, delay);
  }

  window.addEventListener("feynman:event", (e) => {
    const ev = e.detail;
    if (!ev || ev.type !== "feedback.ready") return;
    if (!currentAttemptId || ev.attempt_id !== currentAttemptId) return;
    $("feedback").textContent = ev.text || "";
    if (advancing && advanceTimer) {
      clearTimeout(advanceTimer);
      advanceTimer = setTimeout(advanceNow, 6000);
    }
  });

  async function advanceNow() {
    if (advanceTimer) { clearTimeout(advanceTimer); advanceTimer = null; }
    if (!lastResult) return;
    const r = lastResult;
    lastResult = null;
    if (r.position >= r.target_questions) await endSession();
    else await nextQuestion();
  }

  async function endSession() {
    if (!sessionId) return;
    if (offlineSession) {
      const userId = offlineUserId;
      const r = {
        session_id: sessionId,
        mode: "drill",
        attempts: offlineReviewAttempts,
      };
      renderReview(r);
      document.querySelectorAll(".screen").forEach((el) => el.classList.add("hidden"));
      $("screen-review").classList.remove("hidden");
      sessionId = null;
      resetOfflineSession();
      sessionPosition = 0;
      sessionTargetQuestions = 0;
      if (userId) backgroundOfflineSync(userId);
      return;
    }
    const endedSessionId = sessionId;
    const r = await fetch("/session/end", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    }).then((res) => res.json());
    r.session_id = endedSessionId;
    renderReview(r);
    document.querySelectorAll(".screen").forEach((el) => el.classList.add("hidden"));
    $("screen-review").classList.remove("hidden");
    sessionId = null;
    currentSessionPlan = null;
    sessionPosition = 0;
    sessionTargetQuestions = 0;
    if (window.feynmanUser?.refreshFlags) window.feynmanUser.refreshFlags();
  }

  // ---- shared formatting helpers ----------------------------------------

  function fmt(v) {
    if (v == null) return "—";
    if (typeof v === "number" && Number.isInteger(v)) return v.toString();
    if (typeof v === "number") return v.toFixed(2).replace(/\.?0+$/, "");
    return String(v);
  }

  function escapeHtml(s) {
    return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function masteryClass(m) {
    if (m == null) return "m-none";
    if (m < 0.5) return "m-low";
    if (m < 0.75) return "m-mid";
    return "m-high";
  }

  function masteryBar(m) {
    const pct = m == null ? 0 : Math.round(m * 100);
    return `<div class="mastery-bar"><div class="${masteryClass(m)}" style="width:${pct}%"></div><span>${pct}%</span></div>`;
  }

  function renderSessionPlan(plan) {
    const el = $("session-plan");
    if (!el) return;
    if (!plan) {
      el.classList.add("hidden");
      el.innerHTML = "";
      return;
    }
    const focus = plan.focus?.length
      ? plan.focus.map((f) => `<strong>${escapeHtml(f)}</strong>`).join(", ")
      : escapeHtml(plan.intent || "Exploratory session");
    el.innerHTML = `<div class="plan-head">Today’s focus</div>
      <div class="plan-focus">${focus}</div>
      <div class="plan-sub">${escapeHtml(plan.mix || "")}</div>`;
    el.classList.remove("hidden");
  }

  function sparkline(data, key, max) {
    if (!data || !data.length) return "";
    const w = 80, h = 18;
    const values = data.map((d) => (d[key] == null ? null : Number(d[key])));
    const real = values.filter((v) => v != null);
    if (!real.length) return "";
    const maxV = max != null ? max : Math.max(...real, 0.001);
    const minV = max != null ? 0 : Math.min(...real, 0);
    const range = maxV - minV || 1;
    const pts = values
      .map((v, i) => v == null ? null : `${(i / Math.max(1, values.length - 1)) * w},${h - ((v - minV) / range) * h}`)
      .filter(Boolean)
      .join(" ");
    return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
      <polyline points="${pts}" fill="none" stroke="currentColor" stroke-width="1.5"/>
    </svg>`;
  }

  function attemptsTable(attempts, includeLevel = false) {
    if (!attempts.length) return "<p class='muted'>No attempts.</p>";
    const lvlHead = includeLevel ? "<th>Lvl</th>" : "";
    let html = `<table><thead><tr><th>#</th><th>Skill</th>${lvlHead}<th>Prompt</th><th>You</th><th>Expected</th><th class="num">Latency</th><th></th></tr></thead><tbody>`;
    for (const x of attempts) {
      const cls = x.skipped ? "skipped" : x.correct ? "correct" : "wrong";
      const mark = x.skipped ? "—" : x.correct ? "✓" : "✗";
      const params = parseParams(x.parameters);
      const lvlCell = includeLevel ? `<td>${params?.level ?? "—"}</td>` : "";
      html += `<tr>
        <td>${x.position_in_session}</td>
        <td>${x.skill_name || x.skill_id}</td>
        ${lvlCell}
        <td>${escapeHtml(x.prompt_text)}</td>
        <td>${x.parsed_answer != null ? fmt(x.parsed_answer) : ""}</td>
        <td>${fmt(x.expected_answer)}</td>
        <td class="num">${x.resolution_latency_ms != null ? fmtSec(x.resolution_latency_ms) : ""}</td>
        <td><span class="mark ${cls}">${mark}</span></td>
      </tr>`;
    }
    return html + "</tbody></table>";
  }

  function parseParams(raw) {
    if (!raw) return null;
    if (typeof raw === "object") return raw;
    try { return JSON.parse(raw); } catch { return null; }
  }

  // ---- review screen -----------------------------------------------------

  function renderReview(r) {
    const a = r.attempts || [];
    const isEval = r.mode === "eval";
    $("review-title").textContent = isEval ? "Baseline complete" : "Session review";

    if (!a.length) { $("review").textContent = "No attempts."; return; }
    const total = a.length;
    const correct = a.filter((x) => x.correct).length;
    const skipped = a.filter((x) => x.skipped).length;
    const lats = a.map((x) => x.resolution_latency_ms).filter((v) => v != null).sort((x, y) => x - y);
    const median = lats.length ? lats[Math.floor(lats.length / 2)] : null;
    const accPct = Math.round((correct / total) * 100);

    let html = `<div class="big-metrics">
      <div class="big-metric"><div class="bm-val ${accClass(correct / total)}">${accPct}%</div><div class="bm-label">accuracy · ${correct}/${total}${skipped ? ` · ${skipped} skipped` : ""}</div></div>
      <div class="big-metric"><div class="bm-val">${fmtSec(median)}</div><div class="bm-label">median response</div></div>
    </div>`;

    const perSkill = perSkillSummary(a);
    if (perSkill.length) {
      const wins = perSkill.filter((s) => s.acc >= 0.9 && (s.target == null || s.median == null || s.median <= s.target));
      const losses = perSkill.filter((s) => s.acc < 0.9 || (s.target != null && s.median != null && s.median > s.target));

      if (wins.length) {
        html += `<h3 class="section">Where you did well</h3><ul class="review-bullets good">${
          wins.map((s) => `<li><strong>${escapeHtml(s.name)}</strong> — ${Math.round(s.acc * 100)}% (${s.correct}/${s.total})${s.median != null ? `, ${fmtSec(s.median)}` : ""}</li>`).join("")
        }</ul>`;
      }
      if (losses.length) {
        html += `<h3 class="section">Where to improve</h3><ul class="review-bullets bad">${
          losses.map((s) => {
            const accStr = `${Math.round(s.acc * 100)}% (${s.correct}/${s.total})`;
            const slow = s.target != null && s.median != null && s.median > s.target
              ? ` · ${fmtSec(s.median)} vs target ${fmtSec(s.target)}` : (s.median != null ? ` · ${fmtSec(s.median)}` : "");
            return `<li><strong>${escapeHtml(s.name)}</strong> — ${accStr}${slow}</li>`;
          }).join("")
        }</ul>`;
      }

      html += `<h3 class="section">By skill</h3>
        <table class="metrics"><thead><tr><th>Skill</th><th class="num">Accuracy</th><th class="num">Median</th><th class="num">Target</th></tr></thead><tbody>${
          perSkill.map((s) => `<tr>
            <td><strong>${escapeHtml(s.name)}</strong></td>
            <td class="num ${accClass(s.acc)}">${Math.round(s.acc * 100)}% <span class="muted small">(${s.correct}/${s.total})</span></td>
            <td class="num ${s.target != null && s.median != null && s.median > s.target ? "val-bad" : ""}">${fmtSec(s.median)}</td>
            <td class="num muted">${fmtSec(s.target)}</td>
          </tr>`).join("")
        }</tbody></table>`;
    }

    if (isEval) html += renderBaselineMatrix(a);

    html += `<details class="review-details">
      <summary>Attempt-by-attempt details (${total})</summary>
      ${attemptsTable(a, /*includeLevel*/ true)}
    </details>`;

    html += renderFeedbackList(r.session_id, a);

    $("review").innerHTML = html;
  }

  function perSkillSummary(attempts) {
    const by = {};
    for (const x of attempts) {
      const id = x.skill_id || "unknown";
      if (!by[id]) by[id] = { name: x.skill_name || id, total: 0, correct: 0, lats: [], target: null };
      by[id].total++;
      if (x.correct) by[id].correct++;
      if (x.resolution_latency_ms != null) by[id].lats.push(x.resolution_latency_ms);
      if (x.target_latency_ms != null) by[id].target = x.target_latency_ms;
    }
    return Object.values(by).map((s) => {
      const sorted = s.lats.sort((p, q) => p - q);
      const median = sorted.length ? sorted[Math.floor(sorted.length / 2)] : null;
      return { name: s.name, total: s.total, correct: s.correct, acc: s.correct / s.total, median, target: s.target };
    }).sort((p, q) => q.total - p.total);
  }

  function accClass(acc) {
    if (acc == null) return "";
    if (acc >= 0.9) return "val-good";
    if (acc >= 0.7) return "val-warn";
    return "val-bad";
  }

  function renderFeedbackList(sessionId, attempts) {
    if (!attempts || !attempts.length) return "";
    const rows = attempts.map((x) => {
      const cls = x.skipped ? "skipped" : x.correct ? "correct" : "wrong";
      const mark = x.skipped ? "—" : x.correct ? "✓" : "✗";
      const sid = sessionId || x.local_session_id || "";
      const attemptId = x.id == null ? "" : String(x.id);
      const attemptClientId = x.client_id || "";
      const userId = x.user_id || "";
      return `<div class="fb-row" data-session-id="${escapeHtml(sid)}" data-attempt-id="${escapeHtml(attemptId)}" data-attempt-client-id="${escapeHtml(attemptClientId)}" data-user-id="${escapeHtml(userId)}">
        <span class="fb-mark ${cls}">${mark}</span>
        <span class="fb-prompt">${escapeHtml(x.prompt_text)}</span>
        <button class="fb-thumb" data-thumb="1" aria-label="Thumbs up">👍</button>
        <button class="fb-thumb" data-thumb="-1" aria-label="Thumbs down">👎</button>
        <input type="text" class="fb-reason" placeholder="why? (optional)" maxlength="200">
        <button class="fb-save tiny">save</button>
        <span class="fb-status muted small"></span>
      </div>`;
    }).join("");
    return `<h3 class="section">Rate these questions <span class="muted small">(optional, helps tune future drills)</span></h3>
      <div id="fb-list">${rows}</div>`;
  }

  async function queueFeedbackForSync(row, body, isTextSave) {
    const status = row.querySelector(".fb-status");
    const input = row.querySelector(".fb-reason");
    const saveBtn = row.querySelector(".fb-save");
    const user = window.feynmanUser?.getCurrent?.();
    const userId = row.dataset.userId || user?.id || offlineUserId;
    if (!userId || !offlineAvailable() || !window.feynmanOffline?.queueFeedback) {
      throw new Error("offline queue unavailable");
    }
    await window.feynmanOffline.queueFeedback(userId, {
      session_id: String(body.session_id || "").startsWith("offline:") ? null : body.session_id,
      local_session_id: String(body.session_id || "").startsWith("offline:") ? body.session_id : null,
      attempt_id: body.attempt_id || null,
      attempt_client_id: body.attempt_client_id || null,
      thumb: body.thumb == null ? null : body.thumb,
      reason: body.reason || null,
    });
    if (status) status.textContent = "queued · syncs online";
    if (isTextSave) {
      row.classList.add("fb-saved");
      if (input) input.disabled = true;
      if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = "queued"; }
    }
    updateOfflineStatus(userId);
    backgroundOfflineSync(userId);
  }

  async function postFeedback(row, body) {
    const status = row.querySelector(".fb-status");
    const input = row.querySelector(".fb-reason");
    const saveBtn = row.querySelector(".fb-save");
    const isTextSave = body.reason != null;
    if (status) status.textContent = "saving…";
    if (isTextSave) {
      if (input) input.disabled = true;
      if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = "saving…"; }
    }
    const offlineFeedback = String(body.session_id || "").startsWith("offline:");
    try {
      if (offlineFeedback) {
        await queueFeedbackForSync(row, body, isTextSave);
        return;
      }
      const res = await fetch("/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = new Error(String(res.status));
        err.status = res.status;
        throw err;
      }
      if (status) status.textContent = "saved ✓";
      if (isTextSave) {
        row.classList.add("fb-saved");
        if (saveBtn) saveBtn.textContent = "saved";
      }
    } catch (e) {
      if (!offlineFeedback && e.status && e.status < 500) {
        if (status) status.textContent = "couldn't save — retry";
        if (isTextSave) {
          if (input) input.disabled = false;
          if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = "save"; }
        }
        return;
      }
      try {
        await queueFeedbackForSync(row, body, isTextSave);
      } catch {
        if (status) status.textContent = "couldn't save — retry";
        if (isTextSave) {
          if (input) input.disabled = false;
          if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = "save"; }
        }
      }
    }
  }

  document.addEventListener("click", (e) => {
    const thumb = e.target.closest(".fb-thumb");
    if (thumb) {
      const row = thumb.closest(".fb-row");
      if (!row || !row.dataset.sessionId) return;
      const value = Number(thumb.dataset.thumb);
      row.querySelectorAll(".fb-thumb").forEach((b) => {
        b.disabled = true;
        b.classList.toggle("selected", b === thumb);
      });
      const aid = row.dataset.attemptId ? Number(row.dataset.attemptId) : null;
      postFeedback(row, {
        session_id: row.dataset.sessionId,
        attempt_id: aid,
        attempt_client_id: row.dataset.attemptClientId || null,
        thumb: value,
      });
      return;
    }
    const save = e.target.closest(".fb-save");
    if (save) {
      const row = save.closest(".fb-row");
      if (!row || !row.dataset.sessionId) return;
      const input = row.querySelector(".fb-reason");
      const reason = (input?.value || "").trim();
      if (!reason) return;
      const aid = row.dataset.attemptId ? Number(row.dataset.attemptId) : null;
      postFeedback(row, {
        session_id: row.dataset.sessionId,
        attempt_id: aid,
        attempt_client_id: row.dataset.attemptClientId || null,
        reason,
      });
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const input = e.target.closest(".fb-reason");
    if (!input) return;
    const row = input.closest(".fb-row");
    if (!row || !row.dataset.sessionId) return;
    const reason = (input.value || "").trim();
    if (!reason) return;
    e.preventDefault();
    const aid = row.dataset.attemptId ? Number(row.dataset.attemptId) : null;
    postFeedback(row, {
      session_id: row.dataset.sessionId,
      attempt_id: aid,
      attempt_client_id: row.dataset.attemptClientId || null,
      reason,
    });
  });

  function renderSessionAnalysis(a) {
    const plan = a.plan || {};
    const roleStats = a.role_stats || {};
    const focusRows = a.focus_stats || [];
    const gaps = a.fluency_gaps || [];
    const slowestCorrect = a.slowest_correct || [];
    const focus = plan.focus?.length ? plan.focus.map(escapeHtml).join(", ") : "exploratory mix";
    const roleBits = ["theme", "related", "retention", "exploration"].map((role) => {
      const r = roleStats[role];
      if (!r || !r.total) return "";
      const label = role === "theme" ? "Focused" : role === "related" ? "Related" : role === "retention" ? "Retention" : "Exploration";
      return `<div class="stat"><div class="label">${label}</div><div class="value">${r.correct}/${r.total}</div><div class="sub">${fmtSec(r.median_latency_ms)}</div></div>`;
    }).filter(Boolean).join("");
    const focusBits = focusRows.slice(0, 3).map((r) =>
      `<li><strong>${escapeHtml(r.display)}</strong>: ${r.correct}/${r.total} correct · correct median ${fmtSec(r.median_correct_latency_ms || r.median_latency_ms)}${r.target_ms ? ` vs target ${fmtSec(r.target_ms)}` : ""}</li>`
    ).join("");
    const gapBits = gaps.slice(0, 3).map((r) => {
      const prior = r.baseline_median_latency_ms ? ` Prior median: ${fmtSec(r.baseline_median_latency_ms)}${r.baseline_n ? ` over n=${r.baseline_n}` : ""}.` : "";
      const why = r.interpretation ? ` This looks like ${escapeHtml(r.interpretation)}.` : "";
      return `<li><strong>${escapeHtml(r.display)}</strong> was correct but not fluent: median ${fmtSec(r.median_correct_latency_ms)} vs target ${fmtSec(r.target_ms)}.${prior}${why}</li>`;
    }).join("");
    const slowBits = slowestCorrect.slice(0, 3).map((r) => {
      const gap = r.gap_ms != null && r.gap_ms > 0 ? ` · ${fmtSec(r.gap_ms)} over target` : "";
      return `<li><strong>${escapeHtml(r.display)}</strong>: ${fmtSec(r.latency_ms)}${gap}</li>`;
    }).join("");
    const moved = (a.moved || []).slice(0, 2).map((x) => `<li>${escapeHtml(x)}</li>`).join("");
    const weak = (a.still_weak || []).slice(0, 2).map((x) => `<li>${escapeHtml(x)}</li>`).join("");
    const _FEAT_LABELS = {has_borrow: "borrow", has_carry: "carry", operation: "operation"};
    const stratBits = (a.stratification || []).map((s) => {
      const rowBits = s.rows.map((r) =>
        `<li>${escapeHtml(r.label)}: ${r.correct}/${r.total} correct · ${fmtSec(r.median_latency_ms)}</li>`
      ).join("");
      return `<ul class="analysis-list">${rowBits}</ul>`;
    }).join("");
    const patternBits = (a.patterns || []).map((p) => {
      const feat = _FEAT_LABELS[p.feature_key] || p.feature_key;
      const val = p.feature_value === true ? "yes" : p.feature_value === false ? "no" : escapeHtml(String(p.feature_value));
      return `<li><strong>${escapeHtml(p.skill_id)}</strong> (${feat}=${val}): ${p.ratio.toFixed(1)}× slower · ${fmtSec(p.group_median_ms)} vs ${fmtSec(p.baseline_ms)} (n=${p.n})</li>`;
    }).join("");
    return `<div class="analysis">
      <h3 class="section">What we drilled</h3>
      <p class="analysis-intent">${escapeHtml(plan.intent || "This session had no fixed plan.")}</p>
      <p class="muted small">Focus: ${focus}. ${escapeHtml(plan.mix || "")}</p>
      ${roleBits ? `<div class="summary compact">${roleBits}</div>` : ""}
      ${focusBits ? `<ul class="analysis-list">${focusBits}</ul>` : ""}
      ${gapBits ? `<h3 class="section tight">Correct but slow</h3><ul class="analysis-list">${gapBits}</ul>` : ""}
      ${slowBits ? `<h3 class="section tight">Slowest correct answers</h3><ul class="analysis-list">${slowBits}</ul>` : ""}
      ${moved ? `<h3 class="section tight">What moved</h3><ul class="analysis-list">${moved}</ul>` : ""}
      ${weak ? `<h3 class="section tight">Still weak</h3><ul class="analysis-list">${weak}</ul>` : ""}
      ${stratBits ? `<h3 class="section tight">This session by type</h3>${stratBits}` : ""}
      ${patternBits ? `<h3 class="section tight">Patterns across all sessions</h3><ul class="analysis-list">${patternBits}</ul>` : ""}
      <p class="next-up">${escapeHtml(a.next_time || "")}</p>
    </div>`;
  }

  function renderNoticed(d) {
    if (!d) return "";
    const conf = d.confidence || "low";
    if (!d.total_attempts || conf === "low" || !d.focus?.length) {
      return `<div class="noticed noticed-cold">
        <h3 class="section">What I noticed</h3>
        <p class="muted small">Diagnosis confidence is still low — ${d.total_attempts || 0} attempts overall. The system needs more data before it can call out specific weaknesses with confidence.</p>
      </div>`;
    }
    const bullets = [];
    for (const f of d.focus.slice(0, 2)) {
      bullets.push(`<li><strong>${escapeHtml(f.display)}</strong> is slow — median ${fmtSec(f.median_latency_ms)} vs. target ${fmtSec(f.target_ms)} over n=${f.n}. ${confChip(f.confidence)}</li>`);
    }
    if (d.regression) {
      const r = d.regression;
      bullets.push(`<li><strong>${escapeHtml(r.display)}</strong> regressed — was ${fmtSec(r.old_median_ms)}, now ${fmtSec(r.recent_median_ms)}.</li>`);
    } else if (d.notable_mastered) {
      const n = d.notable_mastered;
      bullets.push(`<li><strong>${escapeHtml(n.display)}</strong> is mastered — ${fmtSec(n.median_latency_ms)} vs. target ${fmtSec(n.target_ms)}.</li>`);
    }
    const focusList = d.focus.slice(0, 2).map((f) => escapeHtml(f.display)).join(" and ");
    return `<div class="noticed">
      <h3 class="section">What I noticed</h3>
      <ul class="noticed-list">${bullets.join("")}</ul>
      <p class="next-up">Next session will focus on <strong>${focusList}</strong>.</p>
    </div>`;
  }

  function renderBaselineMatrix(attempts) {
    const by = {};
    for (const a of attempts) {
      const params = parseParams(a.parameters) || {};
      const lvl = params.level || 0;
      const key = `${a.skill_id}|${lvl}`;
      if (!by[key]) by[key] = { skill: a.skill_name || a.skill_id, level: lvl, total: 0, correct: 0, lats: [] };
      by[key].total++;
      if (a.correct) by[key].correct++;
      if (a.resolution_latency_ms) by[key].lats.push(a.resolution_latency_ms);
    }
    const rows = Object.values(by).sort((x, y) =>
      x.skill === y.skill ? x.level - y.level : x.skill.localeCompare(y.skill)
    );
    let html = `<h3 class="section">Baseline by skill × level</h3>
      <table class="matrix"><thead><tr><th>Skill</th><th>Level</th><th>Acc</th><th class="num">Median</th></tr></thead><tbody>`;
    for (const r of rows) {
      const med = r.lats.length ? r.lats.sort((x, y) => x - y)[Math.floor(r.lats.length / 2)] : null;
      html += `<tr><td>${r.skill}</td><td>${r.level}</td><td>${r.correct}/${r.total}</td><td class="num">${med != null ? fmtSec(med) : ""}</td></tr>`;
    }
    return html + "</tbody></table>";
  }

  // ---- profile screen ----------------------------------------------------

  async function loadProfile() {
    const user = window.feynmanUser?.getCurrent?.();
    if (!user) { $("profile-content").innerHTML = "<p class='muted'>No user selected.</p>"; return; }
    $("profile-user").textContent = `· ${user.name}`;
    $("profile-content").innerHTML = "<p class='muted'>Loading…</p>";
    const data = await fetch(`/profile/${user.id}`).then((r) => r.json());

    const totalAttempts = (data.skills || []).reduce((s, sk) => s + (sk.attempt_count || 0), 0);
    if (!totalAttempts) {
      $("profile-content").innerHTML = "<p class='muted'>No data yet — take a baseline first.</p>";
      return;
    }

    let html = "";

    // --- Next drills (what the scheduler would prioritize) ---
    if (data.next_drills?.length) {
      html += `<div class="profile-section">
        <h3 class="section">Highest-value drills</h3>
        <p class="muted small">What the scheduler will prioritize next. Gap = how far above target latency.</p>
        <table><thead><tr><th>Fact</th><th>Skill</th><th class="num">Median</th><th class="num">Target</th><th class="num">Gap</th><th class="num">Acc</th><th class="num">n</th><th>Conf.</th></tr></thead><tbody>`;
      for (const d of data.next_drills) {
        const gapPct = Math.round(d.gap_ratio * 100);
        html += `<tr>
          <td><strong>${escapeHtml(d.display)}</strong></td>
          <td class="muted">${d.skill_id}</td>
          <td class="num latency-hot">${fmtSec(d.median_latency_ms)}</td>
          <td class="num muted">${fmtSec(d.target_ms)}</td>
          <td class="num ${gapPct > 100 ? 'val-bad' : gapPct > 50 ? 'val-warn' : 'val-ok'}">+${gapPct}%</td>
          <td class="num ${d.accuracy < 0.7 ? 'val-bad' : d.accuracy < 0.9 ? 'val-warn' : ''}">${Math.round(d.accuracy * 100)}%</td>
          <td class="num muted">${d.n}</td>
          <td>${confChip(confidenceFor(d.n))}</td>
        </tr>`;
      }
      html += "</tbody></table></div>";
    }

    // --- Multiplication factor families (×7 facts overall, etc.) ---
    if (data.factor_families?.length) {
      html += `<div class="profile-section">
        <h3 class="section">Multiplication, by factor</h3>
        <p class="muted small">A fact like 6×7 contributes to both ×6 and ×7. The bottleneck is usually one or two specific factors.</p>
        <table><thead><tr><th>Family</th><th class="num">Median</th><th class="num">Acc</th><th class="num">n</th><th>Conf.</th></tr></thead><tbody>`;
      for (const f of data.factor_families) {
        html += `<tr>
          <td><strong>${escapeHtml(f.display)}</strong></td>
          <td class="num latency-hot">${fmtSec(f.median_latency_ms)}</td>
          <td class="num ${f.accuracy < 0.7 ? 'val-bad' : f.accuracy < 0.9 ? 'val-warn' : ''}">${Math.round(f.accuracy * 100)}%</td>
          <td class="num muted">${f.n}</td>
          <td>${confChip(f.confidence)}</td>
        </tr>`;
      }
      html += "</tbody></table></div>";
    }

    // --- Slowest facts ---
    if (data.slowest_facts?.length) {
      html += `<div class="profile-section">
        <h3 class="section">Slowest facts</h3>
        <table><thead><tr><th>Fact</th><th>Skill</th><th class="num">Median</th><th class="num">Acc</th><th class="num">n</th><th>Conf.</th></tr></thead><tbody>`;
      for (const f of data.slowest_facts) {
        html += `<tr>
          <td><strong>${escapeHtml(f.display)}</strong></td>
          <td class="muted">${f.skill_id}</td>
          <td class="num latency-hot">${fmtSec(f.median_latency_ms)}</td>
          <td class="num ${f.accuracy < 0.7 ? 'val-bad' : f.accuracy < 0.9 ? 'val-warn' : ''}">${Math.round(f.accuracy * 100)}%</td>
          <td class="num muted">${f.n}</td>
          <td>${confChip(f.confidence || confidenceFor(f.n))}</td>
        </tr>`;
      }
      html += "</tbody></table></div>";
    }

    // --- Worst accuracy ---
    if (data.worst_accuracy?.length) {
      html += `<div class="profile-section">
        <h3 class="section">Most errors</h3>
        <table><thead><tr><th>Fact</th><th>Skill</th><th class="num">Acc</th><th class="num">Median</th><th class="num">n</th><th>Conf.</th></tr></thead><tbody>`;
      for (const f of data.worst_accuracy) {
        html += `<tr>
          <td><strong>${escapeHtml(f.display)}</strong></td>
          <td class="muted">${f.skill_id}</td>
          <td class="num val-bad">${Math.round(f.accuracy * 100)}%</td>
          <td class="num">${fmtSec(f.median_latency_ms)}</td>
          <td class="num muted">${f.n}</td>
          <td>${confChip(f.confidence || confidenceFor(f.n))}</td>
        </tr>`;
      }
      html += "</tbody></table></div>";
    }

    // --- Regressions ---
    if (data.regressions?.length) {
      html += `<div class="profile-section">
        <h3 class="section">Regressions</h3>
        <p class="muted small">Facts where recent latency is notably worse than earlier performance.</p>
        <table><thead><tr><th>Fact</th><th class="num">Was</th><th class="num">Now</th><th class="num">Slower by</th></tr></thead><tbody>`;
      for (const r of data.regressions) {
        html += `<tr>
          <td><strong>${escapeHtml(r.display)}</strong></td>
          <td class="num">${fmtSec(r.old_median_ms)}</td>
          <td class="num latency-hot">${fmtSec(r.recent_median_ms)}</td>
          <td class="num val-bad">+${Math.round((r.regression_ratio - 1) * 100)}%</td>
        </tr>`;
      }
      html += "</tbody></table></div>";
    }

    // --- Per-skill summary (compact) ---
    if (data.skills?.length) {
      html += `<div class="profile-section">
        <h3 class="section">By operation</h3>
        <table><thead><tr><th>Skill</th><th class="num">Acc</th><th class="num">Median</th><th class="num">Target</th><th class="num">n</th></tr></thead><tbody>`;
      for (const s of data.skills) {
        const accPct = s.rolling_accuracy != null ? Math.round(s.rolling_accuracy * 100) + "%" : "—";
        const med = fmtSec(s.median_latency_ms);
        const target = fmtSec(s.target_latency_ms);
        html += `<tr>
          <td><strong>${escapeHtml(s.display_name)}</strong></td>
          <td class="num">${accPct}</td>
          <td class="num">${med}</td>
          <td class="num muted">${target}</td>
          <td class="num muted">${s.attempt_count}</td>
        </tr>`;
      }
      html += "</tbody></table></div>";
    }

    $("profile-content").innerHTML = html;
  }

  // ---- leaderboard screen ------------------------------------------------

  async function loadLeaderboard() {
    $("leaderboard-content").innerHTML = "<p class='muted'>Loading…</p>";
    const data = await fetch("/leaderboard").then((r) => r.json());
    const users = data.users || [];
    const skills = data.skills || [];
    if (!users.length) { $("leaderboard-content").innerHTML = "<p class='muted'>No users yet.</p>"; return; }

    const sortedUsers = [...users].sort((a, b) => (b.overall_mastery || 0) - (a.overall_mastery || 0));

    let html = `<table class="leaderboard"><thead><tr>
      <th>Player</th>
      <th>Overall</th>
      ${skills.map((s) => `<th>${escapeHtml(s.display_name)}</th>`).join("")}
      <th class="num">Attempts</th>
    </tr></thead><tbody>`;
    for (const u of sortedUsers) {
      const overall = u.overall_mastery;
      html += `<tr>
        <td><strong>${escapeHtml(u.name)}</strong>${u.has_completed_eval ? "" : " <span class='muted'>(no baseline)</span>"}</td>
        <td>${masteryBar(overall)}</td>
        ${skills.map((s) => {
          const sk = u.per_skill[s.id] || {};
          const m = sk.mastery;
          const pct = m == null ? "—" : Math.round(m * 100) + "%";
          const lat = sk.median_latency_ms != null ? `<div class='muted small'>${fmtSec(sk.median_latency_ms)} · n=${sk.attempt_count}</div>` : "";
          return `<td><span class="${masteryClass(m)} pill">${pct}</span>${lat}</td>`;
        }).join("")}
        <td class="num">${u.total_attempts}</td>
      </tr>`;
    }
    html += "</tbody></table>";
    $("leaderboard-content").innerHTML = html;
  }

  // ---- input bindings ----------------------------------------------------

  // Pointer Events: works on touch, mouse, and pen consistently. Avoids the
  // touchstart-preventDefault passive-listener trap on Android Chrome.
  $("typed-answer")?.addEventListener("submit", (e) => {
    e.preventDefault();
    submitTypedAnswer($("typed-answer-input").value);
  });
  $("typed-answer-input")?.addEventListener("input", () => {
    if (!typedOnsetTs) typedOnsetTs = Date.now() / 1000;
  });
  $("btn-skip-typed")?.addEventListener("click", () => submitTypedAnswer("skip"));
  $("btn-skip-voice")?.addEventListener("click", () => submitVoiceSkip());

  const ptt = $("btn-ptt");
  ptt.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    try { ptt.setPointerCapture(e.pointerId); } catch {}
    startRecording();
  });
  ptt.addEventListener("pointerup", (e) => {
    e.preventDefault();
    try { ptt.releasePointerCapture(e.pointerId); } catch {}
    stopRecording();
  });
  ptt.addEventListener("pointercancel", () => stopRecording());
  // Block the long-press context menu on Android.
  ptt.addEventListener("contextmenu", (e) => e.preventDefault());

  let spaceDown = false;
  let volumeKeyDown = false;
  window.addEventListener("keydown", (e) => {
    if (e.code === "Space" && !spaceDown && !$("btn-ptt").disabled) {
      e.preventDefault();
      spaceDown = true;
      startRecording();
    }
    // Volume keys work as PTT on Android Chrome when a non-input element has focus.
    if ((e.code === "AudioVolumeUp" || e.code === "AudioVolumeDown") && !$("btn-ptt").disabled) {
      e.preventDefault();
      if (!volumeKeyDown) { volumeKeyDown = true; startRecording(); }
    }
  });
  window.addEventListener("keyup", (e) => {
    if (e.code === "Space" && spaceDown) {
      e.preventDefault();
      spaceDown = false;
      stopRecording();
    }
    if (e.code === "AudioVolumeUp" || e.code === "AudioVolumeDown") {
      e.preventDefault();
      volumeKeyDown = false;
      stopRecording();
    }
  });

  $("btn-quick-drill").addEventListener("click", () => startSession("drill", 5));
  $("btn-start-eval").addEventListener("click", () => startSession("eval"));
  $("btn-start-drill").addEventListener("click", () => startSession("drill"));
  $("btn-end").addEventListener("click", endSession);
  $("btn-next").addEventListener("click", advanceNow);
  $("btn-restart-eval").addEventListener("click", () => startSession("eval"));
  $("btn-restart-drill").addEventListener("click", () => startSession("drill", 5));

  document.querySelectorAll(".navlink").forEach((b) => {
    b.addEventListener("click", () => showScreen(b.dataset.screen));
  });

  // Settings screen.
  $("btn-settings")?.addEventListener("click", () => showScreen("screen-settings"));
  $("btn-settings-save")?.addEventListener("click", () => {
    const saved = window.feynmanSettings.setApiBase($("setting-api-base").value);
    $("setting-api-base").value = saved;
    $("settings-status").textContent = "Saved.";
  });
  $("btn-settings-reset")?.addEventListener("click", () => {
    const def = window.feynmanSettings.getDefaultApiBase();
    window.feynmanSettings.setApiBase("");
    $("setting-api-base").value = def;
    $("settings-status").textContent = "Reset to default.";
  });
  $("btn-settings-test")?.addEventListener("click", async () => {
    $("settings-status").textContent = "Testing…";
    try {
      const v = await window.feynmanSettings.test($("setting-api-base").value);
      $("settings-status").textContent = `Connected ✓ — backend at commit ${v.sha || "?"}`;
    } catch (e) {
      $("settings-status").textContent = `Failed: ${e.message || e}`;
    }
  });
  $("btn-notifications-enable")?.addEventListener("click", enableNotificationReminders);
  $("btn-notifications-disable")?.addEventListener("click", disableNotificationReminders);

  // Debug pane toggle (visible everywhere; collapses the right column on
  // desktop, opens an overlay on mobile).
  const dbgToggle = $("btn-debug-toggle");
  if (dbgToggle) {
    dbgToggle.addEventListener("click", () => {
      document.body.classList.toggle("debug-collapsed");
    });
    // Default on narrow screens: debug collapsed.
    if (window.matchMedia("(max-width: 900px)").matches) {
      document.body.classList.add("debug-collapsed");
    }
  }

  window.feynmanOffline?.onOnline?.(() => {
    const user = window.feynmanUser?.getCurrent?.();
    if (user) backgroundOfflineSync(user.id);
  });
  window.feynmanNotifications?.onAction?.(() => {
    const user = window.feynmanUser?.getCurrent?.();
    if (user) backgroundOfflineSync(user.id);
    showScreen("screen-start");
  });

  refreshStartScreen();
})();
