// User picker: stores active user in localStorage, fetches list from server,
// manages the top-right dropdown and "+ Add player" flow.
(function () {
  const STORAGE_KEY = "feynman.user_id";
  const USERS_CACHE_KEY = "feynman.users_cache";
  const $ = (id) => document.getElementById(id);

  const state = {
    users: [],
    currentId: null,
    loadError: null,
  };

  function current() {
    return state.users.find((u) => u.id === state.currentId) || null;
  }

  function storedUserId() {
    try { return localStorage.getItem(STORAGE_KEY) || ""; } catch { return ""; }
  }

  function readCachedUsers() {
    try {
      const parsed = JSON.parse(localStorage.getItem(USERS_CACHE_KEY) || "[]");
      return Array.isArray(parsed) ? parsed.filter((u) => u && u.id) : [];
    } catch {
      return [];
    }
  }

  function writeCachedUsers(users) {
    try { localStorage.setItem(USERS_CACHE_KEY, JSON.stringify(users || [])); } catch {}
  }

  function fallbackUsers() {
    const cached = readCachedUsers();
    if (cached.length) return cached;
    const stored = storedUserId();
    return stored ? [{ id: stored, name: "You", has_completed_eval: true, cached: true }] : [];
  }

  function chooseCurrent(users) {
    let stored = storedUserId();
    if (!users.find((u) => u.id === stored)) stored = null;
    if (!stored && users.length > 0) stored = users[0].id;
    state.currentId = stored;
    if (stored) localStorage.setItem(STORAGE_KEY, stored);
  }

  function useFallbackUsers() {
    state.users = fallbackUsers();
    chooseCurrent(state.users);
  }

  async function fetchUsers() {
    try {
      const res = await fetch("/users");
      if (!res.ok) throw new Error("HTTP " + res.status);
      const users = await res.json();
      if (!Array.isArray(users)) throw new Error("invalid users response");
      state.users = users;
      state.loadError = null;
      chooseCurrent(state.users);
      writeCachedUsers(state.users);
    } catch (e) {
      state.loadError = e;
      useFallbackUsers();
    }
    renderPicker();
    notifyChange();
    return state.users;
  }

  function renderPicker() {
    const u = current();
    $("btn-user").textContent = u ? `${u.name} ▾` : state.loadError ? "Server offline" : "No users";
    const menu = $("user-menu");
    menu.innerHTML = "";
    for (const player of state.users) {
      const row = document.createElement("button");
      row.className = "menu-row" + (player.id === state.currentId ? " active" : "");
      const evalDot = player.has_completed_eval ? "●" : "○";
      row.innerHTML = `<span>${escapeHtml(player.name)}</span><span class="dot" title="${player.has_completed_eval ? "baseline done" : "no baseline yet"}">${evalDot}</span>`;
      row.addEventListener("click", () => {
        setCurrent(player.id);
        closeMenu();
      });
      menu.appendChild(row);
    }
    if (state.loadError) {
      const retry = document.createElement("button");
      retry.className = "menu-row";
      retry.textContent = "Retry connection";
      retry.addEventListener("click", () => {
        fetchUsers();
        closeMenu();
      });
      menu.appendChild(retry);
    }
    const sep = document.createElement("div");
    sep.className = "menu-sep";
    menu.appendChild(sep);
    const add = document.createElement("button");
    add.className = "menu-row add";
    add.textContent = "+ Add player";
    add.addEventListener("click", async () => {
      closeMenu();
      const name = prompt("Player name?");
      if (!name) return;
      try {
        const u = await fetch("/users", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: name.trim() }),
        }).then((r) => r.json());
        if (u.id) {
          state.users.push(u);
          setCurrent(u.id);
        } else {
          alert(u.detail || "Couldn't add player.");
        }
      } catch (e) {
        alert("Couldn't add player: " + e);
      }
    });
    menu.appendChild(add);
  }

  function setCurrent(id) {
    state.currentId = id;
    localStorage.setItem(STORAGE_KEY, id);
    const users = state.users.filter((u) => u && u.id);
    if (users.length) writeCachedUsers(users);
    renderPicker();
    notifyChange();
  }

  function notifyChange() {
    const u = current();
    window.dispatchEvent(new CustomEvent("feynman:user-changed", { detail: u }));
  }

  function openMenu() {
    $("user-menu").classList.remove("hidden");
  }
  function closeMenu() {
    $("user-menu").classList.add("hidden");
  }
  function toggleMenu() {
    $("user-menu").classList.toggle("hidden");
  }

  function escapeHtml(s) {
    return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // Public API on window
  window.feynmanUser = {
    getCurrent: current,
    list: () => state.users,
    refresh: fetchUsers,
    refreshFlags: async () => {
      await fetchUsers();
    },
  };

  $("btn-user").addEventListener("click", (e) => {
    e.stopPropagation();
    toggleMenu();
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#user-picker")) closeMenu();
  });

  window.addEventListener("online", () => fetchUsers());
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && state.loadError) fetchUsers();
  });

  useFallbackUsers();
  renderPicker();
  notifyChange();
  window.feynmanUser.ready = fetchUsers();
})();
