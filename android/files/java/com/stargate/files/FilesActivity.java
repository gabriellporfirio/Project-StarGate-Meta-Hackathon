package com.stargate.files;

import android.app.Activity;
import android.graphics.Color;
import android.os.Bundle;
import android.view.Gravity;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.io.File;

/** StarGate Files: shows the access code and serves uploads on loopback. */
public class FilesActivity extends Activity {
    private static final int PORT = 8080;

    private FilesServer server;
    private TextView codeView;
    private TextView addrView;
    private TextView countView;

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
        title.setText("StarGate Files");
        title.setTextSize(36);
        title.setTextColor(Color.WHITE);
        title.setGravity(Gravity.CENTER);
        root.addView(title);

        codeView = new TextView(this);
        codeView.setText("Access code: starting…");
        codeView.setTextSize(20);
        codeView.setTextColor(Color.WHITE);
        codeView.setGravity(Gravity.CENTER);
        root.addView(codeView);

        addrView = new TextView(this);
        addrView.setText("Address: starting…");
        addrView.setTextSize(16);
        addrView.setTextColor(Color.LTGRAY);
        addrView.setGravity(Gravity.CENTER);
        root.addView(addrView);

        countView = new TextView(this);
        countView.setText("Uploads: starting…");
        countView.setTextSize(16);
        countView.setTextColor(Color.LTGRAY);
        countView.setGravity(Gravity.CENTER);
        root.addView(countView);

        setContentView(root);
        startServer();
    }

    private void startServer() {
        final File uploads = new File(getFilesDir(), "uploads");
        final File codeFile = new File(getFilesDir(), "access-code");
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    server = new FilesServer(uploads, codeFile, PORT,
                            FilesServer.DEFAULT_MAX_UPLOAD_BYTES,
                            FilesServer.DEFAULT_MAX_TOTAL_BYTES);
                    server.start();
                    show("Access code: " + server.getCode(),
                            "Address: " + server.getBindAddress()
                                    + ":" + server.getPort(),
                            uploadsSummary());
                } catch (final Exception failed) {
                    show("Access code: failed to start",
                            "Address: " + failed.getMessage(),
                            "Uploads: unknown");
                }
            }
        }, "files-start").start();
    }

    private String uploadsSummary() {
        try {
            File[] files = server.getUploadDir().toFile().listFiles();
            int count = 0;
            long total = 0;
            if (files != null) {
                for (File file : files) {
                    if (file.isFile()) {
                        count++;
                        total += file.length();
                    }
                }
            }
            return "Uploads: " + count + " files, " + total + " bytes";
        } catch (Exception ignored) {
            return "Uploads: unknown";
        }
    }

    private void show(final String code, final String addr,
                      final String count) {
        runOnUiThread(new Runnable() {
            @Override
            public void run() {
                codeView.setText(code);
                addrView.setText(addr);
                countView.setText(count);
            }
        });
    }

    @Override
    protected void onDestroy() {
        try {
            if (server != null) {
                server.stop();
            }
        } catch (Exception ignored) {
            // Shutting down only.
        }
        super.onDestroy();
    }
}
