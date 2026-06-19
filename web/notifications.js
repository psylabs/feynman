// Local reminder scheduling for the bundled Capacitor app.
(function () {
  "use strict";

  var ENABLED_KEY = "feynman.notifications.enabled";
  var REMINDERS = [
    {
      id: 601,
      label: "6:00 AM seed sync",
      title: "Feynman",
      body: "Load today's offline prompts.",
      schedule: { on: { hour: 6, minute: 0 } },
      extra: { kind: "seed-sync" },
    },
    {
      id: 602,
      label: "6:00 PM drill",
      title: "Feynman",
      body: "Run a quick drill.",
      schedule: { on: { hour: 18, minute: 0 } },
      extra: { kind: "drill" },
    },
  ];

  function createFeynmanNotifications(options) {
    options = options || {};
    var root = options.window || window;
    var storage = options.storage || root.localStorage || localStorage;
    var actionListener = null;

    function plugin() {
      if (options.plugin !== undefined) return options.plugin;
      var cap = root.Capacitor;
      return (cap && cap.Plugins && cap.Plugins.LocalNotifications) || root.LocalNotifications || null;
    }

    function isNative() {
      if (typeof options.isNative === "function") return !!options.isNative();
      var cap = root.Capacitor;
      return !!(
        (cap && typeof cap.isNativePlatform === "function" && cap.isNativePlatform()) ||
        root.FEYNMAN_BUNDLED
      );
    }

    function isSupported() {
      return !!plugin() && isNative();
    }

    function isEnabled() {
      try { return storage.getItem(ENABLED_KEY) === "1"; } catch (e) { return false; }
    }

    function setEnabled(value) {
      try {
        if (value) storage.setItem(ENABLED_KEY, "1");
        else storage.removeItem(ENABLED_KEY);
      } catch (e) {}
    }

    function reminderDescriptors() {
      return REMINDERS.map(function (reminder) {
        return {
          id: reminder.id,
          title: reminder.title,
          body: reminder.body,
          schedule: reminder.schedule,
          extra: reminder.extra,
          autoCancel: true,
        };
      });
    }

    function reminderIds() {
      return REMINDERS.map(function (reminder) { return { id: reminder.id }; });
    }

    function summary() {
      return REMINDERS.map(function (reminder) { return reminder.label; }).join(", ");
    }

    async function ensurePermission(notifications) {
      var status = null;
      if (typeof notifications.checkPermissions === "function") {
        status = await notifications.checkPermissions();
      }
      if (!status || (status.display !== "granted" && status.display !== "limited")) {
        if (typeof notifications.requestPermissions !== "function") {
          throw new Error("notification permission unavailable");
        }
        status = await notifications.requestPermissions();
      }
      if (status && status.display !== "granted" && status.display !== "limited") {
        throw new Error("notification permission denied");
      }
    }

    async function enable() {
      var notifications = plugin();
      if (!notifications || !isNative()) return { enabled: false, reason: "unavailable" };
      await ensurePermission(notifications);
      if (typeof notifications.cancel === "function") {
        await notifications.cancel({ notifications: reminderIds() });
      }
      await notifications.schedule({ notifications: reminderDescriptors() });
      setEnabled(true);
      return { enabled: true, summary: summary() };
    }

    async function disable() {
      var notifications = plugin();
      if (notifications && typeof notifications.cancel === "function") {
        await notifications.cancel({ notifications: reminderIds() });
      }
      setEnabled(false);
      return { enabled: false };
    }

    async function onAction(handler) {
      var notifications = plugin();
      if (!notifications || !isNative() || typeof notifications.addListener !== "function") return null;
      if (actionListener) return actionListener;
      actionListener = await notifications.addListener("localNotificationActionPerformed", handler);
      return actionListener;
    }

    return {
      enable: enable,
      disable: disable,
      isEnabled: isEnabled,
      isSupported: isSupported,
      onAction: onAction,
      summary: summary,
      reminderDescriptors: reminderDescriptors,
    };
  }

  window.createFeynmanNotifications = createFeynmanNotifications;
  window.feynmanNotifications = createFeynmanNotifications();
})();
