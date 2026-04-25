// Live debug pane — subscribes to /events (SSE), renders structured events.
(function () {
  const events = document.getElementById("events");
  const clearBtn = document.getElementById("btn-clear");
  const statusDot = document.getElementById("dbg-status");

  function render(payload) {
    const div = document.createElement("div");
    div.className = "evt";
    const ts =
      payload.ts > 0
        ? new Date(payload.ts * 1000).toISOString().split("T")[1].slice(0, 12)
        : "—";
    const type = payload.type || "?";
    const rest = { ...payload };
    delete rest.ts;
    delete rest.type;
    const restStr = Object.keys(rest).length ? JSON.stringify(rest) : "";
    div.innerHTML =
      `<span class="t">${ts}</span>` +
      `<span class="ty">${type}</span>` +
      (restStr ? `<code>${escapeHtml(restStr)}</code>` : "");
    events.appendChild(div);
    while (events.childElementCount > 500) {
      events.removeChild(events.firstChild);
    }
    events.scrollTop = events.scrollHeight;
  }

  function escapeHtml(s) {
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function connect() {
    const es = new EventSource("/events");
    es.onopen = () => {
      statusDot.classList.remove("off");
      statusDot.classList.add("on");
    };
    es.onerror = () => {
      statusDot.classList.remove("on");
      statusDot.classList.add("off");
    };
    es.onmessage = (e) => {
      try {
        const payload = JSON.parse(e.data);
        render(payload);
        // Make events available to app.js for live UI updates.
        window.dispatchEvent(new CustomEvent("feynman:event", { detail: payload }));
      } catch {
        /* ignore */
      }
    };
  }

  clearBtn.addEventListener("click", () => {
    events.innerHTML = "";
  });

  connect();
})();
