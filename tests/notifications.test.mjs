import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

function loadNotificationsModule({ plugin = null, native = true } = {}) {
  const localStore = new Map();
  const calls = [];
  const listeners = {};
  const notificationPlugin = plugin || {
    checkPermissions: async () => ({ display: "prompt" }),
    requestPermissions: async () => ({ display: "granted" }),
    cancel: async (options) => { calls.push({ method: "cancel", options }); },
    schedule: async (options) => {
      calls.push({ method: "schedule", options });
      return { notifications: options.notifications.map((n) => ({ id: n.id })) };
    },
    addListener: async (eventName, handler) => {
      listeners[eventName] = handler;
      calls.push({ method: "addListener", eventName });
      return { remove: async () => {} };
    },
  };

  const context = {
    console,
    window: {
      Capacitor: {
        Plugins: notificationPlugin ? { LocalNotifications: notificationPlugin } : {},
        isNativePlatform: () => native,
      },
      FEYNMAN_BUNDLED: native,
    },
    localStorage: {
      getItem: (key) => localStore.get(key) || null,
      setItem: (key, value) => { localStore.set(key, String(value)); },
      removeItem: (key) => { localStore.delete(key); },
    },
  };
  context.window.localStorage = context.localStorage;
  vm.runInNewContext(readFileSync("web/notifications.js", "utf8"), context);
  return {
    api: context.window.feynmanNotifications,
    calls,
    localStore,
    listeners,
  };
}

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

test("notification reminders request permission and schedule the daily prompts", async () => {
  const { api, calls, localStore } = loadNotificationsModule();

  const result = await api.enable();

  assert.equal(result.enabled, true);
  assert.equal(localStore.get("feynman.notifications.enabled"), "1");
  const scheduleCall = calls.find((call) => call.method === "schedule");
  assert.ok(scheduleCall);
  assert.deepEqual(
    plain(scheduleCall.options.notifications.map((notification) => notification.id)),
    [601, 602],
  );
  assert.deepEqual(plain(scheduleCall.options.notifications[0].schedule), { on: { hour: 6, minute: 0 } });
  assert.deepEqual(plain(scheduleCall.options.notifications[1].schedule), { on: { hour: 18, minute: 0 } });
  assert.equal(api.summary(), "6:00 AM seed sync, 6:00 PM drill");
});

test("notification reminders can be disabled and remove the persisted flag", async () => {
  const { api, calls, localStore } = loadNotificationsModule();

  await api.enable();
  await api.disable();

  assert.equal(api.isEnabled(), false);
  assert.equal(localStore.has("feynman.notifications.enabled"), false);
  const cancelCalls = calls.filter((call) => call.method === "cancel");
  assert.equal(cancelCalls.length, 2);
  assert.deepEqual(
    plain(cancelCalls.at(-1).options.notifications.map((notification) => notification.id)),
    [601, 602],
  );
});

test("notification action listeners proxy plugin taps", async () => {
  const { api, listeners } = loadNotificationsModule();
  let action = null;

  await api.onAction((event) => { action = event; });
  listeners.localNotificationActionPerformed({ notification: { extra: { kind: "drill" } } });

  assert.deepEqual(plain(action), { notification: { extra: { kind: "drill" } } });
});

test("notifications stay unavailable outside the bundled app", async () => {
  const { api, calls, localStore } = loadNotificationsModule({ native: false });

  const result = await api.enable();

  assert.equal(api.isSupported(), false);
  assert.deepEqual(plain(result), { enabled: false, reason: "unavailable" });
  assert.equal(calls.length, 0);
  assert.equal(localStore.has("feynman.notifications.enabled"), false);
});
