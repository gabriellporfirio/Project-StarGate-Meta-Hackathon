package com.stargate.launcher;

import android.app.Activity;
import android.graphics.Color;
import android.os.Bundle;
import android.view.Gravity;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Minimal StarGate home screen for the M4 spike (Path B). */
public class HomeActivity extends Activity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER);
        root.setBackgroundColor(Color.BLACK);

        TextView title = new TextView(this);
        title.setText("StarGate");
        title.setTextSize(48);
        title.setTextColor(Color.WHITE);
        title.setGravity(Gravity.CENTER);
        root.addView(title);

        TextView services = new TextView(this);
        services.setText("Services: Files, Assistant");
        services.setTextSize(20);
        services.setTextColor(Color.LTGRAY);
        services.setGravity(Gravity.CENTER);
        root.addView(services);

        setContentView(root);
    }
}
