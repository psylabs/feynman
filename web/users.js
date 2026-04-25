// User picker: stores active user in localStorage, fetches list from server,
// manages the top-right dropdown and "+ Add player" flow.
(function () {
  const STORAGE_KEY = "feynman.user_id";
  const $ = (id) => document.getElementById(id);

  const state = {
    users: [],
    currentId: null,
  };

  function current() {
    return state.users.find((u) => u.id === state.currentId) || null;
  }

  async function fetchUsers() {
    state.users = await fetch("/users").then((r) => r.json());
    let stored = localStorage.getItem(STORAGE_KEY);
    if (!state.users.find((u) => u.id === stored)) stored = null;
    if (!stored && state.users.length > 0) stored = state.users[0].id;
    state.currentId = stored;
    if (stored) localStorage.setItem(STORAGE_KEY, stored);
    renderPicker();
    notifyChange();
  }

  function renderPicker() {
    const u = current();
    $("btn-user").textContent = u ? `${u.name} ▾` : "No users";
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
      const fresh = await fetch("/users").then((r) => r.json());
      state.users = fresh;
      renderPicker();
      notifyChange();
    },
  };

  $("btn-user").addEventListener("click", (e) => {
    e.stopPropagation();
    toggleMenu();
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#user-picker")) closeMenu();
  });

  fetchUsers();
})();
