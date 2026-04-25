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

  function showScreen(id) {
    document.querySelectorAll(".screen").forEach((el) => el.classList.add("hidden"));
    $(id).classList.remove("hidden");
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

  window.addEventListener("feynman:user-changed", refreshStartScreen);

  // ---- session lifecycle -------------------------------------------------

  async function startSession(mode) {
    const user = window.feynmanUser?.getCurrent?.();
    if (!user) {
      alert("Pick a player first.");
      return;
    }
    const r = await fetch("/session/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: user.id, mode }),
    }).then((res) => res.json());
    if (r.detail) {
      alert(r.detail);
      return;
    }
    sessionId = r.session_id;
    currentMode = r.mode;
    showScreen("screen-session");
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
    } catch (e) {
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
    } catch (e) {
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
    } catch (e) {
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
    if (r.skipped) {
      label = "Skipped";
      cls = "skipped";
    } else if (r.correct) {
      label = "✓ Correct";
      cls = "correct";
    } else {
      label = "✗ Wrong";
      cls = "wrong";
    }
    const detail = r.skipped
      ? ""
      : `  ·  you said ${fmt(r.parsed)}, expected ${fmt(r.expected)}`;
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
    if (advanceTimer) {
      clearTimeout(advanceTimer);
      advanceTimer = null;
    }
    if (!lastResult) return;
    const r = lastResult;
    lastResult = null;
    if (r.position >= r.target_questions) {
      await endSession();
    } else {
      await nextQuestion();
    }
  }

  function fmt(v) {
    if (v == null) return "(unparsed)";
    if (Number.isInteger(v)) return v.toString();
    return v.toFixed(2).replace(/\.?0+$/, "");
  }

  async function endSession() {
    if (!sessionId) return;
    const r = await fetch("/session/end", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    }).then((res) => res.json());
    renderReview(r);
    showScreen("screen-review");
    sessionId = null;
    // baseline-completed flag may have flipped — refresh user list.
    if (window.feynmanUser?.refreshFlags) window.feynmanUser.refreshFlags();
  }

  function renderReview(r) {
    const a = r.attempts || [];
    const isEval = r.mode === "eval";
    $("review-title").textContent = isEval ? "Baseline complete" : "Session review";

    if (!a.length) {
      $("review").textContent = "No attempts.";
      return;
    }
    const total = a.length;
    const correct = a.filter((x) => x.correct).length;
    const skipped = a.filter((x) => x.skipped).length;
    const lats = a
      .map((x) => x.resolution_latency_ms)
      .filter((v) => v != null)
      .sort((x, y) => x - y);
    const median = lats.length ? lats[Math.floor(lats.length / 2)] : null;

    let html = `<div class="summary">
      <div class="stat"><div class="label">Correct</div><div class="value">${correct}/${total}</div></div>
      <div class="stat"><div class="label">Skipped</div><div class="value">${skipped}</div></div>
      <div class="stat"><div class="label">Median latency</div><div class="value">${median != null ? median + " ms" : "—"}</div></div>
    </div>`;

    if (isEval) {
      html += renderBaselineMatrix(a);
    }

    html += `<table><thead><tr>
      <th>#</th><th>Skill</th><th>Lvl</th><th>Prompt</th><th>You</th><th>Expected</th><th class="num">Latency</th><th></th>
    </tr></thead><tbody>`;
    for (const x of a) {
      const cls = x.skipped ? "skipped" : x.correct ? "correct" : "wrong";
      const mark = x.skipped ? "—" : x.correct ? "✓" : "✗";
      const params = parseParams(x.parameters);
      const lvl = params?.level ?? "—";
      html += `<tr>
        <td>${x.position_in_session}</td>
        <td>${x.skill_name || x.skill_id}</td>
        <td>${lvl}</td>
        <td>${escapeHtml(x.prompt_text)}</td>
        <td>${x.parsed_answer != null ? fmt(x.parsed_answer) : ""}</td>
        <td>${fmt(x.expected_answer)}</td>
        <td class="num">${x.resolution_latency_ms != null ? x.resolution_latency_ms : ""}</td>
        <td><span class="mark ${cls}">${mark}</span></td>
      </tr>`;
    }
    html += "</tbody></table>";
    $("review").innerHTML = html;
  }

  function renderBaselineMatrix(attempts) {
    // Group by (skill, level)
    const by = {};
    for (const a of attempts) {
      const params = parseParams(a.parameters) || {};
      const lvl = params.level || 0;
      const key = `${a.skill_id}|${lvl}`;
      if (!by[key]) by[key] = { skill: a.skill_name || a.skill_id, level: lvl, total: 0, correct: 0, lats: [] };
      by[key].total += 1;
      if (a.correct) by[key].correct += 1;
      if (a.resolution_latency_ms) by[key].lats.push(a.resolution_latency_ms);
    }
    const rows = Object.values(by).sort((x, y) =>
      x.skill === y.skill ? x.level - y.level : x.skill.localeCompare(y.skill)
    );
    let html = `<h3 class="section">Baseline by skill × level</h3>
      <table class="matrix"><thead><tr>
        <th>Skill</th><th>Level</th><th>Acc</th><th class="num">Median ms</th>
      </tr></thead><tbody>`;
    for (const r of rows) {
      const acc = `${r.correct}/${r.total}`;
      const med = r.lats.length
        ? r.lats.sort((x, y) => x - y)[Math.floor(r.lats.length / 2)]
        : null;
      html += `<tr><td>${r.skill}</td><td>${r.level}</td><td>${acc}</td><td class="num">${med ?? ""}</td></tr>`;
    }
    html += "</tbody></table>";
    return html;
  }

  function parseParams(raw) {
    if (!raw) return null;
    if (typeof raw === "object") return raw;
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  }

  function escapeHtml(s) {
    return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // ---- input bindings ----------------------------------------------------

  $("btn-ptt").addEventListener("mousedown", startRecording);
  $("btn-ptt").addEventListener("mouseup", stopRecording);
  $("btn-ptt").addEventListener("mouseleave", stopRecording);
  $("btn-ptt").addEventListener("touchstart", (e) => {
    e.preventDefault();
    startRecording();
  });
  $("btn-ptt").addEventListener("touchend", (e) => {
    e.preventDefault();
    stopRecording();
  });

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

  refreshStartScreen();
})();
