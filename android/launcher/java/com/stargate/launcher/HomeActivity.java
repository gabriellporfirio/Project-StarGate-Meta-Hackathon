package com.stargate.launcher;

import android.app.Activity;
import android.graphics.Color;
import android.os.Bundle;
import android.view.Gravity;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;

/**
 * StarGate home screen. Shows "StarGate", the selected services from
 * the handoff only (baked into assets/stargate-build.json by the
 * build script), the build ID, and any label. Requests no permissions.
 */
public class HomeActivity extends Activity {
    private static final int MAX_CONFIG_BYTES = 64 * 1024;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER);
        root.setBackgroundColor(Color.BLACK);
        int pad = (int) (16 * getResources().getDisplayMetrics().density);
        root.setPadding(pad, pad, pad, pad);

        TextView title = new TextView(this);
        title.setText("StarGate");
        title.setTextSize(48);
        title.setTextColor(Color.WHITE);
        title.setGravity(Gravity.CENTER);
        root.addView(title);

        BuildConfig config = readConfig();
        for (String service : config.services) {
            TextView line = new TextView(this);
            line.setText("\u2022 " + displayName(service));
            line.setTextSize(22);
            line.setTextColor(Color.LTGRAY);
            line.setGravity(Gravity.CENTER);
            root.addView(line);
        }

        TextView build = new TextView(this);
        build.setText("Build: " + config.buildId);
        build.setTextSize(16);
        build.setTextColor(Color.LTGRAY);
        build.setGravity(Gravity.CENTER);
        root.addView(build);

        for (String label : config.labels) {
            TextView line = new TextView(this);
            line.setText(label);
            line.setTextSize(16);
            line.setTextColor(Color.YELLOW);
            line.setGravity(Gravity.CENTER);
            root.addView(line);
        }

        setContentView(root);
    }

    private static String displayName(String service) {
        if (service.equals("files")) {
            return "Files";
        }
        if (service.equals("assistant")) {
            return "Assistant";
        }
        return service;
    }

    private BuildConfig readConfig() {
        try {
            InputStream in = getAssets().open("stargate-build.json");
            ByteArrayOutputStream raw = new ByteArrayOutputStream();
            byte[] buf = new byte[4096];
            int total = 0;
            while (true) {
                int got = in.read(buf);
                if (got < 0) {
                    break;
                }
                total += got;
                if (total > MAX_CONFIG_BYTES) {
                    break;
                }
                raw.write(buf, 0, got);
            }
            in.close();
            JSONObject json = new JSONObject(raw.toString("UTF-8"));
            JSONArray services = json.getJSONArray("services");
            JSONArray labels = json.getJSONArray("labels");
            String[] serviceNames = new String[services.length()];
            for (int i = 0; i < services.length(); i++) {
                serviceNames[i] = services.getString(i);
            }
            String[] labelTexts = new String[labels.length()];
            for (int i = 0; i < labels.length(); i++) {
                labelTexts[i] = labels.getString(i);
            }
            return new BuildConfig(serviceNames, json.getString("build_id"),
                    labelTexts);
        } catch (Exception missing) {
            return new BuildConfig(new String[0], "unknown",
                    new String[0]);
        }
    }

    private static class BuildConfig {
        final String[] services;
        final String buildId;
        final String[] labels;

        BuildConfig(String[] services, String buildId, String[] labels) {
            this.services = services;
            this.buildId = buildId;
            this.labels = labels;
        }
    }
}
