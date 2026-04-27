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
    const r = await fetch("/session/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: user.id, mode }),
    }).then((res) => res.json());
    if (r.detail) return alert(r.detail);
    sessionId = r.session_id;
    currentMode = r.mode;
    document.querySelectorAll(".screen").forEach((el) => el.classList.add("hidden"));
    $("screen-session").classList.remove("hidden");
    $("mode-tag").textContent = currentMode === "eval" ? "Baseline" : "Drill";
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
    const timing = r.resolution_latency_ms != null ? `  ·  ${r.resolution_latency_ms}ms` : "";
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
        <td class="num">${x.resolution_latency_ms != null ? x.resolution_latency_ms : ""}</td>
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

    let html = `<div class="summary">
      <div class="stat"><div class="label">Correct</div><div class="value">${correct}/${total}</div></div>
      <div class="stat"><div class="label">Skipped</div><div class="value">${skipped}</div></div>
      <div class="stat"><div class="label">Median latency</div><div class="value">${median != null ? median + " ms" : "—"}</div></div>
    </div>`;

    if (isEval) html += renderBaselineMatrix(a);
    html += attemptsTable(a, /*includeLevel*/ true);
    $("review").innerHTML = html;
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
      <table class="matrix"><thead><tr><th>Skill</th><th>Level</th><th>Acc</th><th class="num">Median ms</th></tr></thead><tbody>`;
    for (const r of rows) {
      const med = r.lats.length ? r.lats.sort((x, y) => x - y)[Math.floor(r.lats.length / 2)] : null;
      html += `<tr><td>${r.skill}</td><td>${r.level}</td><td>${r.correct}/${r.total}</td><td class="num">${med ?? ""}</td></tr>`;
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
    if (!data.skills?.length) {
      $("profile-content").innerHTML = "<p class='muted'>No data yet — take a baseline first.</p>";
      return;
    }
    $("profile-content").innerHTML = data.skills.map(renderSkillCard).join("");
  }

  function renderSkillCard(s) {
    const accPct = s.rolling_accuracy != null ? Math.round(s.rolling_accuracy * 100) + "%" : "—";
    const med = s.median_latency_ms != null ? s.median_latency_ms + " ms" : "—";
    const target = s.target_latency_ms ? s.target_latency_ms + " ms" : "—";
    const lvls = ["1", "2", "3"].map((lvl) => {
      const d = s.per_level[lvl];
      if (!d) return `<div class="lvl"><label>L${lvl}</label><span class="muted">no data</span></div>`;
      const pct = Math.round(d.accuracy * 100);
      return `<div class="lvl">
        <label>L${lvl}</label>
        <span class="${pct >= 85 ? 'good' : pct >= 50 ? 'ok' : 'low'}">${d.correct}/${d.n} · ${pct}%</span>
        <span class="muted">${d.median_latency_ms != null ? d.median_latency_ms + "ms" : "—"}</span>
      </div>`;
    }).join("");
    const wrong = s.recent_wrong?.length
      ? `<div class="recent-wrong"><div class="rw-head">Recent missed</div>` +
        s.recent_wrong.map((w) => `<div class="rw-row">
          <span class="prompt">${escapeHtml(w.prompt)}</span>
          <span class="vs"><span class="wrong">${fmt(w.your_answer)}</span> → <span class="correct">${fmt(w.expected)}</span></span>
          ${w.notes ? `<div class="note">${escapeHtml(w.notes)}</div>` : ""}
        </div>`).join("") +
        `</div>`
      : "";
    return `<div class="skill-card">
      <div class="card-head">
        <div class="name">${escapeHtml(s.display_name)}</div>
        <div class="spark-wrap" title="Accuracy across recent sessions">${sparkline(s.history, "accuracy", 1)}</div>
      </div>
      ${masteryBar(s.mastery)}
      <div class="numbers">
        <div><label>Recent acc</label><strong>${accPct}</strong></div>
        <div><label>Median</label><strong>${med}</strong></div>
        <div><label>Target</label><strong>${target}</strong></div>
        <div><label>Attempts</label><strong>${s.attempt_count}</strong></div>
      </div>
      <div class="level-grid">${lvls}</div>
      ${wrong}
    </div>`;
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
          const lat = sk.median_latency_ms != null ? `<div class='muted small'>${sk.median_latency_ms}ms · n=${sk.attempt_count}</div>` : "";
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
