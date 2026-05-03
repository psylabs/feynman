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
  let advancing = false;
  let advanceTimer = null;
  let lastResult = null;
  let currentSessionPlan = null;

  // ---- screen switching --------------------------------------------------

  async function showScreen(id) {
    // Leaving an active session via nav: end it cleanly.
    if (sessionId && id !== "screen-session" && id !== "screen-review") {
      try {
        await fetch("/session/end", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sessionId }),
        });
      } catch {}
      sessionId = null;
    }

    document.querySelectorAll(".screen").forEach((el) => el.classList.add("hidden"));
    $(id).classList.remove("hidden");
    document.querySelectorAll(".navlink").forEach((b) => {
      b.classList.toggle("active", b.dataset.screen === id);
    });

    if (id === "screen-profile") loadProfile();
    if (id === "screen-leaderboard") loadLeaderboard();
    if (id === "screen-start") refreshStartScreen();
  }

  // ---- start screen state ------------------------------------------------

  function refreshStartScreen() {
    const user = window.feynmanUser?.getCurrent?.();
    $("greeting").textContent = user ? `Hi, ${user.name}.` : "Hi.";
    $("diagnosis-teaser").classList.add("hidden");
    if (!user) return;
    if (user.has_completed_eval) {
      $("eval-banner").classList.add("hidden");
      $("btn-start-eval").textContent = "Re-do baseline";
      $("btn-start-eval").classList.remove("primary");
      $("btn-start-eval").classList.add("secondary");
      $("btn-start-drill").classList.add("primary");
      $("btn-start-drill").classList.remove("secondary");
    } else {
      $("eval-banner").classList.remove("hidden");
      $("btn-start-eval").textContent = "Take baseline (20q)";
      $("btn-start-eval").classList.add("primary");
      $("btn-start-eval").classList.remove("secondary");
      $("btn-start-drill").classList.remove("primary");
      $("btn-start-drill").classList.add("secondary");
    }
    loadDiagnosisTeaser(user.id);
  }

  async function loadDiagnosisTeaser(userId) {
    const el = $("diagnosis-teaser");
    try {
      const d = await fetch(`/diagnosis/${userId}`).then((r) => r.json());
      el.innerHTML = renderTeaser(d);
      el.classList.remove("hidden");
    } catch {
      el.classList.add("hidden");
    }
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

  async function startSession(mode) {
    const user = window.feynmanUser?.getCurrent?.();
    if (!user) return alert("Pick a player first.");
    const body = { user_id: user.id, mode };
    if (mode === "drill") {
      const sel = $("drill-length");
      const n = sel ? parseInt(sel.value, 10) : NaN;
      if (Number.isFinite(n)) body.target_questions = n;
    }
    const r = await fetch("/session/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((res) => res.json());
    if (r.detail) return alert(r.detail);
    sessionId = r.session_id;
    currentMode = r.mode;
    currentSessionPlan = r.session_plan || null;
    document.querySelectorAll(".screen").forEach((el) => el.classList.add("hidden"));
    $("screen-session").classList.remove("hidden");
    $("mode-tag").textContent = currentMode === "eval" ? "Baseline" : "Drill";
    renderSessionPlan(currentSessionPlan);
    $("result").textContent = "";
    $("result").className = "";
    $("feedback").textContent = "";
    await nextQuestion();
  }

  async function nextQuestion() {
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
    $("prompt").textContent = "";

    const r = await fetch("/session/next", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    }).then((res) => res.json());

    currentQid = r.qid;
    $("prompt").textContent = r.prompt_text;
    $("position").textContent = `Question ${r.position} of ${r.target_questions}`;

    const audio = new Audio(r.audio_url);
    $("status").textContent = "Listening…";
    audio.addEventListener("ended", () => {
      promptEndTs = Date.now() / 1000;
      $("status").textContent = "Hold to answer (or hold spacebar)";
      $("btn-ptt").disabled = false;
    });
    try {
      await audio.play();
    } catch {
      promptEndTs = Date.now() / 1000;
      $("status").textContent = "Audio blocked — answer now";
      $("btn-ptt").disabled = false;
    }
  }

  async function startRecording() {
    if (recording || $("btn-ptt").disabled || !currentQid) return;
    recording = true;
    onsetTs = Date.now() / 1000;
    audioChunks = [];
    $("btn-ptt").classList.add("recording");
    $("btn-ptt").textContent = "Recording…";

    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      $("status").textContent = "Microphone permission denied";
      recording = false;
      $("btn-ptt").classList.remove("recording");
      $("btn-ptt").textContent = "Press & hold to answer";
      return;
    }

    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) audioChunks.push(e.data);
    };
    mediaRecorder.onstop = async () => {
      const resolutionTs = Date.now() / 1000;
      try {
        if (stream) stream.getTracks().forEach((t) => t.stop());
      } catch {}
      const mime = mediaRecorder.mimeType || "audio/webm";
      const blob = new Blob(audioChunks, { type: mime });
      await submitAnswer(blob, resolutionTs);
    };
    mediaRecorder.start();
  }

  async function stopRecording() {
    if (!recording) return;
    recording = false;
    $("btn-ptt").classList.remove("recording");
    $("btn-ptt").textContent = "Press & hold to answer";
    $("btn-ptt").disabled = true;
    $("status").textContent = "Transcribing…";
    if (mediaRecorder && mediaRecorder.state === "recording") mediaRecorder.stop();
  }

  async function submitAnswer(blob, resolutionTs) {
    const fd = new FormData();
    fd.append("session_id", sessionId);
    fd.append("qid", currentQid);
    fd.append("prompt_end_ts", String(promptEndTs));
    fd.append("onset_ts", String(onsetTs));
    fd.append("resolution_ts", String(resolutionTs));
    fd.append("audio", blob, "answer.webm");

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
      return;
    }

    lastResult = r;
    currentAttemptId = r.attempt_id || null;

    let label, cls;
    if (r.skipped) { label = "Skipped"; cls = "skipped"; }
    else if (r.correct) { label = "✓ Correct"; cls = "correct"; }
    else { label = "✗ Wrong"; cls = "wrong"; }
    const detail = r.skipped ? "" : `  ·  you said ${fmt(r.parsed)}, expected ${fmt(r.expected)}`;
    const timing = r.resolution_latency_ms != null ? `  ·  ${fmtSec(r.resolution_latency_ms)}` : "";
    $("result").textContent = `${label}${detail}${timing}`;
    $("result").className = cls;
    $("status").textContent = `“${r.transcript || ""}”`;
    $("feedback").textContent = r.feedback_pending ? "thinking…" : "";
    $("btn-next").classList.remove("hidden");

    if (advancing) return;
    advancing = true;
    const delay = r.feedback_pending ? 5000 : 2000;
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
    const r = await fetch("/session/end", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    }).then((res) => res.json());
    renderReview(r);
    document.querySelectorAll(".screen").forEach((el) => el.classList.add("hidden"));
    $("screen-review").classList.remove("hidden");
    sessionId = null;
    currentSessionPlan = null;
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

    const diag = r.diagnosis;
    const analysis = r.session_analysis;
    let html = "";

    // 1. What this session was designed to drill.
    if (!isEval && analysis) html += renderSessionAnalysis(analysis);

    // 2. What I noticed (diagnosis-first; baseline mode skips this since the
    //    diagnosis was just produced from this session)
    if (!isEval && diag) html += renderNoticed(diag);

    // 3. This session at a glance
    html += `<h3 class="section">This session</h3>
      <div class="summary">
        <div class="stat"><div class="label">Correct</div><div class="value">${correct}/${total}</div></div>
        <div class="stat"><div class="label">Skipped</div><div class="value">${skipped}</div></div>
        <div class="stat"><div class="label">Median latency</div><div class="value">${fmtSec(median)}</div></div>
      </div>`;

    // 4. Baseline matrix (eval only) or attempts table (collapsed by default for drills)
    if (isEval) {
      html += renderBaselineMatrix(a);
      html += attemptsTable(a, /*includeLevel*/ true);
    } else {
      html += `<details class="review-details">
        <summary>Attempt-by-attempt details (${total})</summary>
        ${attemptsTable(a, /*includeLevel*/ true)}
      </details>`;
    }

    $("review").innerHTML = html;
  }

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

  $("btn-ptt").addEventListener("mousedown", startRecording);
  $("btn-ptt").addEventListener("mouseup", stopRecording);
  $("btn-ptt").addEventListener("mouseleave", stopRecording);
  $("btn-ptt").addEventListener("touchstart", (e) => { e.preventDefault(); startRecording(); });
  $("btn-ptt").addEventListener("touchend", (e) => { e.preventDefault(); stopRecording(); });

  let spaceDown = false;
  window.addEventListener("keydown", (e) => {
    if (e.code === "Space" && !spaceDown && !$("btn-ptt").disabled) {
      e.preventDefault();
      spaceDown = true;
      startRecording();
    }
  });
  window.addEventListener("keyup", (e) => {
    if (e.code === "Space" && spaceDown) {
      e.preventDefault();
      spaceDown = false;
      stopRecording();
    }
  });

  $("btn-start-eval").addEventListener("click", () => startSession("eval"));
  $("btn-start-drill").addEventListener("click", () => startSession("drill"));
  $("btn-end").addEventListener("click", endSession);
  $("btn-next").addEventListener("click", advanceNow);
  $("btn-restart-eval").addEventListener("click", () => startSession("eval"));
  $("btn-restart-drill").addEventListener("click", () => startSession("drill"));

  document.querySelectorAll(".navlink").forEach((b) => {
    b.addEventListener("click", () => showScreen(b.dataset.screen));
  });

  refreshStartScreen();
})();
