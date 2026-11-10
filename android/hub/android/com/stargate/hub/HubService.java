package com.stargate.hub;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.IBinder;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Keeps the phone's one server running, screen on or off. The server
 * itself is plain Java ({@link HubServer}); this class only gives it
 * the phone: its storage, its pages, its battery and its model.
 */
public final class HubService extends Service {
    private static final String CHANNEL = "services";
    private static final int MAX_CONFIG_BYTES = 64 * 1024;
    private static final Object LOCK = new Object();
    private static HubServer server;
    private static String screenToken;
    private static String startError;

    /** Ask Android to start (or keep) the service. */
    public static void begin(Context context) {
        Intent intent = new Intent(context, HubService.class);
        try {
            context.startForegroundService(intent);
        } catch (RuntimeException refused) {
            // Android may refuse a background start; the home screen
            // starts the server directly in that case.
        }
    }

    /** The token only this phone's own screen holds. */
    public static String screenToken(Context context) {
        ensure(context.getApplicationContext());
        synchronized (LOCK) {
            return screenToken;
        }
    }

    public static String startError() {
        synchronized (LOCK) {
            return startError;
        }
    }

    /** Start the server once per process. */
    static void ensure(Context app) {
        synchronized (LOCK) {
            if (server != null) {
                return;
            }
            try {
                byte[] raw = new byte[24];
                new SecureRandom().nextBytes(raw);
                StringBuilder token = new StringBuilder();
                for (byte b : raw) {
                    token.append(String.format("%02x", b & 0xff));
                }
                Map<String, Object> config = readConfig(app);
                List<String> services = new ArrayList<String>();
                Object listed = config.get("services");
                if (listed instanceof List) {
                    for (Object id : (List<?>) listed) {
                        services.add(String.valueOf(id));
                    }
                }
                String buildId = Json.str(config, "build_id");
                HubServer made = new HubServer(
                        new File(app.getFilesDir(), "hub"),
                        HubServer.DEFAULT_PORT, assets(app),
                        new AndroidDevice(app),
                        new AndroidAsk(app, config), services,
                        buildId == null ? "unknown" : buildId,
                        token.toString());
                made.start();
                server = made;
                screenToken = token.toString();
                startError = null;
            } catch (IOException | RuntimeException failed) {
                startError = String.valueOf(failed.getMessage());
            }
        }
    }

    private static Assets assets(final Context app) {
        return new Assets() {
            @Override
            public InputStream open(String name) throws IOException {
                return app.getAssets().open("ui/" + name);
            }
        };
    }

    static Map<String, Object> readConfig(Context app) {
        try (InputStream in = app.getAssets().open("stargate-build.json")) {
            ByteArrayOutputStream raw = new ByteArrayOutputStream();
            byte[] buf = new byte[4096];
            int got;
            while ((got = in.read(buf)) >= 0
                    && raw.size() <= MAX_CONFIG_BYTES) {
                raw.write(buf, 0, got);
            }
            return Json.object(raw.toString("UTF-8"));
        } catch (IOException | Json.Bad missing) {
            return Json.map();
        }
    }

    @Override
    public void onCreate() {
        super.onCreate();
        NotificationManager manager = (NotificationManager)
                getSystemService(Context.NOTIFICATION_SERVICE);
        manager.createNotificationChannel(new NotificationChannel(
                CHANNEL, "Services", NotificationManager.IMPORTANCE_MIN));
        Notification note = new Notification.Builder(this, CHANNEL)
                .setSmallIcon(android.R.drawable.stat_notify_sync_noanim)
                .setContentTitle("Services are running")
                .setOngoing(true)
                .build();
        try {
            startForeground(1, note,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } catch (RuntimeException refused) {
            // The server still runs while the home screen is alive.
        }
        ensure(getApplicationContext());
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        ensure(getApplicationContext());
        return START_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
