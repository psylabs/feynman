package com.psylabs.feynman;

import android.view.KeyEvent;
import android.view.WindowManager;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

@CapacitorPlugin(name = "PttKeys")
public class PttKeysPlugin extends Plugin {
    private volatile boolean armed = false;

    @PluginMethod
    public void setArmed(PluginCall call) {
        armed = Boolean.TRUE.equals(call.getBoolean("armed", false));
        call.resolve();
    }

    @PluginMethod
    public void setKeepAwake(PluginCall call) {
        boolean on = Boolean.TRUE.equals(call.getBoolean("on", false));
        getActivity().runOnUiThread(() -> {
            if (on) getActivity().getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            else getActivity().getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        });
        call.resolve();
    }

    boolean handleKey(KeyEvent event) {
        if (!armed || event.getKeyCode() != KeyEvent.KEYCODE_VOLUME_DOWN) return false;
        if (event.getRepeatCount() > 0) return true;
        notifyListeners(event.getAction() == KeyEvent.ACTION_DOWN ? "pttDown" : "pttUp", new JSObject());
        return true;
    }
}
