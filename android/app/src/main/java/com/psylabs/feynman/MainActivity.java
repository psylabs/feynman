package com.psylabs.feynman;

import android.graphics.Color;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.View;

import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        registerPlugin(PttKeysPlugin.class);
        WindowCompat.setDecorFitsSystemWindows(getWindow(), false);
        getWindow().setStatusBarColor(Color.TRANSPARENT);
        getWindow().setNavigationBarColor(Color.TRANSPARENT);
        super.onCreate(savedInstanceState);

        View content = findViewById(android.R.id.content);
        content.setBackgroundColor(Color.parseColor("#0a0b0e"));
        ViewCompat.setOnApplyWindowInsetsListener(content, (view, windowInsets) -> {
            Insets bars = windowInsets.getInsets(WindowInsetsCompat.Type.systemBars());
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            return windowInsets;
        });
        ViewCompat.requestApplyInsets(content);
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        if (getBridge() != null) {
            var handle = getBridge().getPlugin("PttKeys");
            if (handle != null && ((PttKeysPlugin) handle.getInstance()).handleKey(event)) return true;
        }
        return super.dispatchKeyEvent(event);
    }
}
