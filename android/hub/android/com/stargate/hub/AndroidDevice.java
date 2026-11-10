package com.stargate.hub;

import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.net.wifi.WifiManager;
import android.os.BatteryManager;
import android.os.PowerManager;

/** The real phone behind the {@link Device} seam. */
final class AndroidDevice implements Device {
    private final Context app;

    AndroidDevice(Context app) {
        this.app = app;
    }

    private Intent battery() {
        return app.registerReceiver(null,
                new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
    }

    @Override
    public int batteryPercent() {
        Intent state = battery();
        if (state == null) {
            return -1;
        }
        int level = state.getIntExtra(BatteryManager.EXTRA_LEVEL, -1);
        int scale = state.getIntExtra(BatteryManager.EXTRA_SCALE, -1);
        if (level < 0 || scale <= 0) {
            return -1;
        }
        return Math.round(level * 100f / scale);
    }

    @Override
    public boolean pluggedIn() {
        Intent state = battery();
        return state != null
                && state.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) != 0;
    }

    private WifiManager wifi() {
        return (WifiManager) app.getSystemService(Context.WIFI_SERVICE);
    }

    @Override
    public boolean networkOn() {
        WifiManager wifi = wifi();
        return wifi != null && wifi.isWifiEnabled();
    }

    /**
     * Android only lets apps holding its network-settings permission
     * switch Wi-Fi; without it the screen just shows the state.
     */
    @Override
    public boolean canSwitchNetwork() {
        return wifi() != null && app.checkSelfPermission(
                "android.permission.NETWORK_SETTINGS")
                == PackageManager.PERMISSION_GRANTED;
    }

    @Override
    @SuppressWarnings("deprecation")
    public boolean setNetwork(boolean on) {
        try {
            return wifi().setWifiEnabled(on);
        } catch (RuntimeException refused) {
            return false;
        }
    }

    @Override
    public boolean canReboot() {
        return app.checkSelfPermission("android.permission.REBOOT")
                == PackageManager.PERMISSION_GRANTED;
    }

    @Override
    public boolean reboot() {
        try {
            final PowerManager power = (PowerManager)
                    app.getSystemService(Context.POWER_SERVICE);
            new Thread(new Runnable() {
                @Override
                public void run() {
                    try {
                        Thread.sleep(800);
                    } catch (InterruptedException ignored) {
                        // Reboot anyway.
                    }
                    power.reboot(null);
                }
            }, "reboot").start();
            return true;
        } catch (RuntimeException refused) {
            return false;
        }
    }
}
