package com.stargate.assistant;

import android.app.Activity;
import android.os.Bundle;
import android.os.SystemClock;
import android.text.Html;
import android.text.InputType;
import android.text.method.LinkMovementMethod;
import android.view.Gravity;
import android.view.View;
import android.widget.AdapterView;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.TextView;

import java.io.File;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

import org.pytorch.executorch.extension.llm.LlmCallback;
import org.pytorch.executorch.extension.llm.LlmGenerationConfig;
import org.pytorch.executorch.extension.llm.LlmModule;

/**
 * Assistant: asks an on-device ExecuTorch model a question.
 *
 * <p>The .pte and tokenizer files are opened by file path (Route B folder
 * first, then the Route A image folder); they are never APK assets. The
 * Route B folder is created when missing; Route A is read-only image
 * storage and is only opened in place, never created or copied. Both
 * files are SHA-256-checked against the manifest before loading. The
 * question is sent in the Llama 3.2 Instruct chat format (see ChatFormat);
 * generation stops at the end-of-turn and end-of-text tokens, and
 * control-token text never reaches the screen. Every question runs
 * through AskFlow: device-fact questions are answered by the app
 * without calling the model, and every other question resets the
 * runner first so each starts from an empty context. The cloud
 * variant adds a Model drop-down (Local, Meta, Meta open-weight):
 * device-fact questions are still answered by the app in every
 * mode, cloud modes send only the current question, and the screen
 * always shows which mode and model answered. Users bring their own
 * key; the app never tracks money.
 */
public class MainActivity extends Activity {

    private TextView statusView;
    private TextView answerView;
    private TextView statsView;
    private TextView detailsView;
    private TextView pteView;
    private TextView tokView;
    private TextView answeredView;
    private EditText questionBox;
    private Button askButton;
    private Spinner modeBox;
    private EditText modelBox;
    private TextView modeBlurb;
    private KeyStorage keys;
    private EditText metaKeyBox;
    private EditText basetenKeyBox;
    private TextView metaKeyStatus;
    private TextView basetenKeyStatus;

    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final AtomicBoolean busy = new AtomicBoolean(false);
    private volatile LlmModule module;
    private volatile String pteHash = "";
    private volatile String tokHash = "";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (16 * getResources().getDisplayMetrics().density);
        root.setPadding(pad, pad, pad, pad);

        TextView title = new TextView(this);
        title.setText("Assistant");
        title.setTextSize(20);
        root.addView(title);

        TextView modelView = new TextView(this);
        modelView.setText("Model: " + ModelConfig.MODEL_ID);
        modelView.setTextSize(12);
        root.addView(modelView);

        if (ModelConfig.CLOUD_ENABLED) {
            modeBox = new Spinner(this);
            ArrayAdapter<String> modes = new ArrayAdapter<String>(
                    this, android.R.layout.simple_spinner_item,
                    new String[]{CloudSpec.MODE_LOCAL,
                        CloudSpec.MODE_META, CloudSpec.MODE_OPEN});
            modes.setDropDownViewResource(
                    android.R.layout.simple_spinner_dropdown_item);
            modeBox.setAdapter(modes);
            root.addView(modeBox);

            modeBlurb = new TextView(this);
            modeBlurb.setTextSize(12);
            root.addView(modeBlurb);
            showModeBlurb(CloudSpec.MODE_LOCAL);

            modelBox = new EditText(this);
            modelBox.setText(CloudSpec.DEFAULT_META_MODEL);
            modelBox.setHint("Meta model ID");
            modelBox.setVisibility(View.GONE);
            root.addView(modelBox);
            modeBox.setOnItemSelectedListener(
                    new AdapterView.OnItemSelectedListener() {
                        @Override
                        public void onItemSelected(
                                AdapterView<?> parent, View view,
                                int pos, long id) {
                            modelBox.setVisibility(pos == 1
                                    ? View.VISIBLE : View.GONE);
                            String mode = pos == 1
                                    ? CloudSpec.MODE_META
                                    : (pos == 2
                                            ? CloudSpec.MODE_OPEN
                                            : CloudSpec.MODE_LOCAL);
                            showModeBlurb(mode);
                        }

                        @Override
                        public void onNothingSelected(
                                AdapterView<?> parent) {
                        }
                    });
        }

        answeredView = new TextView(this);
        answeredView.setTextSize(12);
        answeredView.setText("Answered by: —");
        root.addView(answeredView);

        pteView = new TextView(this);
        pteView.setTextSize(10);
        pteView.setText("pte sha256: not checked");
        root.addView(pteView);
        tokView = new TextView(this);
        tokView.setTextSize(10);
        tokView.setText("tok sha256: not checked");
        root.addView(tokView);

        statusView = new TextView(this);
        statusView.setText("Starting…");
        root.addView(statusView);

        questionBox = new EditText(this);
        questionBox.setHint("Ask a question");
        questionBox.setSingleLine(false);
        root.addView(questionBox);

        askButton = new Button(this);
        askButton.setText("Ask");
        askButton.setEnabled(false);
        root.addView(askButton);

        if (ModelConfig.CLOUD_ENABLED) {
            keys = new KeystoreKeyStorage(this);
            TextView settingsTitle = new TextView(this);
            settingsTitle.setText("Cloud keys");
            settingsTitle.setTextSize(16);
            root.addView(settingsTitle);
            addKeyRow(root, CloudSpec.META, "Meta");
            addKeyRow(root, CloudSpec.BASETEN, "Baseten");
            refreshKeyStatus();
        }

        Button licensesButton = new Button(this);
        licensesButton.setText("Licenses");
        licensesButton.setOnClickListener(v -> startActivity(
                new android.content.Intent(this, LicensesActivity.class)));
        root.addView(licensesButton);

        ScrollView scroll = new ScrollView(this);
        answerView = new TextView(this);
        answerView.setTextSize(16);
        answerView.setGravity(Gravity.START);
        scroll.addView(answerView);
        LinearLayout.LayoutParams scrollParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1.0f);
        root.addView(scroll, scrollParams);

        statsView = new TextView(this);
        statsView.setTextSize(12);
        root.addView(statsView);

        detailsView = new TextView(this);
        detailsView.setTextSize(10);
        detailsView.setVisibility(View.GONE);
        root.addView(detailsView);

        Button detailsButton = new Button(this);
        detailsButton.setText("Details");
        detailsButton.setOnClickListener(v -> {
            if (detailsView.getVisibility() == View.VISIBLE) {
                detailsView.setVisibility(View.GONE);
            } else {
                detailsView.setVisibility(View.VISIBLE);
            }
        });
        root.addView(detailsButton);

        setContentView(root);

        askButton.setOnClickListener(v -> onAsk());
        worker.execute(this::startUp);
    }

    /** Mode explainer (spec section 1, exact text, links fixed). */
    private void showModeBlurb(String mode) {
        if (modeBlurb == null) {
            return;
        }
        if (CloudSpec.MODE_META.equals(mode)) {
            showCloudBlurb(modeBlurb, CloudSpec.META,
                    CloudSpec.META_DISPLAY);
        } else if (CloudSpec.MODE_OPEN.equals(mode)) {
            showCloudBlurb(modeBlurb, CloudSpec.BASETEN,
                    CloudSpec.BASETEN_DISPLAY);
        } else {
            modeBlurb.setText(CloudSpec.LOCAL_BLURB);
            modeBlurb.setMovementMethod(null);
        }
    }

    private void showCloudBlurb(TextView into, String provider,
            String display) {
        String html = CloudSpec.CLOUD_BLURB + " <a href=\""
                + CloudSpec.termsUrl(provider) + "\">" + display
                + "</a>";
        into.setText(Html.fromHtml(html));
        into.setMovementMethod(LinkMovementMethod.getInstance());
    }

    private void setStatus(final String text) {
        runOnUiThread(() -> statusView.setText(text));
    }

    private void setAnswer(final String text) {
        runOnUiThread(() -> answerView.setText(text));
    }

    private void setStatsLine(final String text) {
        runOnUiThread(() -> statsView.setText(text));
    }

    private void setDetails(final String text) {
        runOnUiThread(() -> detailsView.setText(text));
    }

    private void startUp() {
        File base = getExternalFilesDir(null);
        if (base == null) {
            setStatus("ERROR: app storage is not available, so the "
                    + "Route B model folder cannot be opened.");
            return;
        }
        File routeB = new File(base, ModelConfig.ROUTE_B_LEAF);
        File routeA = new File(ModelConfig.ROUTE_A_DIR);
        // Route B only: create the folder when missing. A folder already
        // owned by someone else is not repaired here; its failure is
        // reported below. Route A is never created or copied.
        String created = ModelFiles.ensureDir(routeB.getAbsolutePath());
        String[] reportB;
        if (created != null) {
            reportB = new String[]{created};
        } else {
            reportB = ModelFiles.checkDir(routeB.getAbsolutePath(),
                    ModelConfig.PTE_NAME, ModelConfig.PTE_BYTES,
                    ModelConfig.TOK_NAME, ModelConfig.TOK_BYTES);
        }
        String[] reportA = ModelFiles.checkDir(routeA.getAbsolutePath(),
                ModelConfig.PTE_NAME, ModelConfig.PTE_BYTES,
                ModelConfig.TOK_NAME, ModelConfig.TOK_BYTES);
        File pte = null;
        File tok = null;
        if (ModelFiles.dirReady(reportB)) {
            pte = new File(routeB, ModelConfig.PTE_NAME);
            tok = new File(routeB, ModelConfig.TOK_NAME);
        } else if (ModelFiles.dirReady(reportA)) {
            pte = new File(routeA, ModelConfig.PTE_NAME);
            tok = new File(routeA, ModelConfig.TOK_NAME);
        }
        if (pte == null) {
            StringBuilder msg = new StringBuilder(
                    "ERROR: model files not found.\n");
            msg.append("Route B: ").append(routeB.getAbsolutePath())
                    .append("\n");
            for (String line : reportB) {
                msg.append("  ").append(line).append("\n");
            }
            msg.append("Route A: ").append(routeA.getAbsolutePath())
                    .append("\n");
            for (String line : reportA) {
                msg.append("  ").append(line).append("\n");
            }
            msg.append("Expected: ").append(ModelConfig.PTE_NAME)
                    .append(" (").append(ModelConfig.PTE_BYTES)
                    .append(" bytes), ").append(ModelConfig.TOK_NAME)
                    .append(" (").append(ModelConfig.TOK_BYTES)
                    .append(" bytes)");
            setStatus(msg.toString());
            return;
        }
        setStatus("Checking file hashes…");
        try {
            pteHash = ManifestVerifier.sha256HexFile(pte.getAbsolutePath());
            tokHash = ManifestVerifier.sha256HexFile(tok.getAbsolutePath());
        } catch (Exception e) {
            setStatus("ERROR: cannot read model files (" + e.getMessage()
                    + ")");
            return;
        }
        final String pteLine = "pte sha256: " + pteHash;
        final String tokLine = "tok sha256: " + tokHash;
        showHashes(pteLine, tokLine);
        if (!pteHash.equals(ModelConfig.PTE_SHA256)) {
            setStatus("ERROR: pte hash mismatch. Expected "
                    + ModelConfig.PTE_SHA256 + " but got " + pteHash
                    + ". Model not loaded.");
            return;
        }
        if (!tokHash.equals(ModelConfig.TOK_SHA256)) {
            setStatus("ERROR: tokenizer hash mismatch. Expected "
                    + ModelConfig.TOK_SHA256 + " but got " + tokHash
                    + ". Model not loaded.");
            return;
        }
        setStatus("Model hashes match. Loading model…");
        long start = SystemClock.elapsedRealtime();
        try {
            LlmModule loaded = new LlmModule(LlmModule.MODEL_TYPE_TEXT,
                    pte.getAbsolutePath(), tok.getAbsolutePath(),
                    ChatFormat.TEMPERATURE);
            loaded.load();
            module = loaded;
        } catch (Exception e) {
            setStatus("ERROR: model failed to load (" + e.getMessage()
                    + ")");
            return;
        }
        long took = SystemClock.elapsedRealtime() - start;
        setStatus("Ready (loaded in " + took + " ms)");
        setStatsLine("Load: " + took + " ms");
        runOnUiThread(() -> askButton.setEnabled(true));
    }

    private void showHashes(final String pteLine, final String tokLine) {
        runOnUiThread(() -> {
            pteView.setText(pteLine);
            tokView.setText(tokLine);
        });
    }

    private void setAnswered(final String mode, final String model) {
        runOnUiThread(() -> answeredView.setText(
                "Answered by: " + ModeLabel.label(mode, model)));
    }

    private String currentMode() {
        if (ModelConfig.CLOUD_ENABLED && modeBox != null
                && modeBox.getSelectedItem() != null) {
            return modeBox.getSelectedItem().toString();
        }
        return CloudSpec.MODE_LOCAL;
    }

    private String currentModel(String mode) {
        if (CloudSpec.MODE_META.equals(mode) && modelBox != null) {
            String typed = modelBox.getText().toString().trim();
            if (!typed.isEmpty()) {
                return typed;
            }
            return CloudSpec.DEFAULT_META_MODEL;
        }
        if (CloudSpec.MODE_OPEN.equals(mode)) {
            return CloudSpec.OPEN_WEIGHT_MODEL;
        }
        return ModelConfig.MODEL_ID;
    }

    private void addKeyRow(LinearLayout root, final String provider,
            String display) {
        final TextView status = new TextView(this);
        status.setTextSize(12);
        root.addView(status);
        final TextView blurb = new TextView(this);
        blurb.setTextSize(12);
        showCloudBlurb(blurb, provider, display);
        root.addView(blurb);
        final EditText paste = new EditText(this);
        paste.setHint("Paste " + display + " key");
        paste.setSingleLine(true);
        paste.setInputType(InputType.TYPE_CLASS_TEXT
                | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        root.addView(paste);
        Button save = new Button(this);
        save.setText("Save " + display + " key");
        root.addView(save);
        Button del = new Button(this);
        del.setText("Delete " + display + " key");
        root.addView(del);
        if (CloudSpec.META.equals(provider)) {
            metaKeyStatus = status;
            metaKeyBox = paste;
        } else {
            basetenKeyStatus = status;
            basetenKeyBox = paste;
        }
        save.setOnClickListener(v -> {
            String pasted = paste.getText().toString().trim();
            if (pasted.isEmpty()) {
                setStatus("ERROR: paste a key first.");
                return;
            }
            try {
                keys.save(provider, pasted);
            } catch (Exception e) {
                setStatus("ERROR: key not saved (" + e.getMessage()
                        + ")");
                return;
            }
            paste.setText("");
            refreshKeyStatus();
            setStatus("Ready");
        });
        del.setOnClickListener(v -> {
            keys.delete(provider);
            refreshKeyStatus();
            setStatus("Ready");
        });
    }

    private void refreshKeyStatus() {
        runOnUiThread(() -> {
            String meta = keys.lastFour(CloudSpec.META);
            metaKeyStatus.setText(meta.isEmpty() ? "Meta: no key"
                    : "Meta: last four " + meta);
            String baseten = keys.lastFour(CloudSpec.BASETEN);
            basetenKeyStatus.setText(baseten.isEmpty()
                    ? "Baseten: no key"
                    : "Baseten: last four " + baseten);
        });
    }

    private void onAsk() {
        if (module == null || !busy.compareAndSet(false, true)) {
            return;
        }
        final String question = questionBox.getText().toString().trim();
        if (question.isEmpty()) {
            busy.set(false);
            setStatus("ERROR: type a question first.");
            return;
        }
        runOnUiThread(() -> askButton.setEnabled(false));
        setAnswer("");
        setStatsLine("");
        setDetails("");
        setStatus("Thinking…");
        final String mode = currentMode();
        final String model = currentModel(mode);
        worker.execute(() -> runQuestion(question, mode, model));
    }

    private void runQuestion(String question, String mode,
            String model) {
        String where = ModeDispatch.route(mode, question);
        if (ModeDispatch.APP.equals(where)) {
            setAnswer(DeviceFacts.ANSWER);
            setStatsLine(DeviceFacts.APP_STATS_LINE);
            setDetails("");
            setAnswered(mode, "app");
            setStatus("Ready");
            runOnUiThread(() -> askButton.setEnabled(true));
            busy.set(false);
            return;
        }
        if (ModeDispatch.LOCAL.equals(where)) {
            runLocalQuestion(question);
            return;
        }
        runCloudQuestion(question, mode, model);
    }

    private void runCloudQuestion(String question, final String mode,
            final String model) {
        final String provider = ModeDispatch.providerFor(mode);
        final long start = SystemClock.elapsedRealtime();
        CloudAsk.ask(provider, model, ChatFormat.SYSTEM_MESSAGE,
                question, keys,
                new HttpNetworkLayer(),
                new CloudAsk.Clock() {
                    @Override
                    public long nowMs() {
                        return SystemClock.elapsedRealtime();
                    }
                },
                new CloudAsk.Sleeper() {
                    @Override
                    public void sleepMs(long ms)
                            throws InterruptedException {
                        Thread.sleep(ms);
                    }
                },
                new CloudAsk.Callback() {
                    @Override
                    public void onAnswer(String text,
                            boolean incomplete, boolean truncated,
                            long promptTokens,
                            long completionTokens) {
                        long took = SystemClock.elapsedRealtime()
                                - start;
                        long total = promptTokens >= 0
                                && completionTokens >= 0
                                        ? promptTokens
                                                + completionTokens
                                        : -1;
                        setAnswer(text);
                        setStatsLine(StatsLine.formatCloud(
                                CloudSpec.display(provider), total,
                                took));
                        setDetails("provider=" + provider
                                + " model=" + model + " in="
                                + promptTokens + " out="
                                + completionTokens + " ms=" + took);
                        setAnswered(mode, model);
                        setStatus(incomplete ? "Ready (incomplete)"
                                : "Ready");
                        runOnUiThread(
                                () -> askButton.setEnabled(true));
                        busy.set(false);
                    }

                    @Override
                    public void onError(String message) {
                        setStatus("ERROR: " + message);
                        runOnUiThread(
                                () -> askButton.setEnabled(true));
                        busy.set(false);
                    }

                    @Override
                    public void onRefused(String message) {
                        setStatus("ERROR: " + message);
                        runOnUiThread(
                                () -> askButton.setEnabled(true));
                        busy.set(false);
                    }
                });
    }

    private void runLocalQuestion(String question) {
        final long start = SystemClock.elapsedRealtime();
        final AnswerAccumulator acc = new AnswerAccumulator();
        final AtomicBoolean stopSent = new AtomicBoolean(false);
        try {
            AskFlow.ask(new LlmRunner(), question,
                    new AskFlow.GenerateCallback() {
                @Override
                public void onResult(String token) {
                    boolean stop = acc.append(token);
                    setAnswer(acc.display());
                    if (stop && stopSent.compareAndSet(false, true)) {
                        worker.execute(() -> {
                            try {
                                module.stop();
                            } catch (Exception ignored) {
                                // Best effort: the display is
                                // already cut at the stop marker.
                            }
                        });
                    }
                }

                @Override
                public void onStats(String stats) {
                    long took = SystemClock.elapsedRealtime() - start;
                    setStatsLine(StatsLine.format(stats, took));
                    setDetails(stats == null ? "" : stats);
                    setAnswered(CloudSpec.MODE_LOCAL,
                            ModelConfig.MODEL_ID);
                    setStatus("Ready");
                    runOnUiThread(() -> askButton.setEnabled(true));
                    busy.set(false);
                }

                @Override
                public void onError(int code, String message) {
                    setStatus("ERROR: generation failed (" + code + ": "
                            + message + ")");
                    runOnUiThread(() -> askButton.setEnabled(true));
                    busy.set(false);
                }

                @Override
                public void onAppAnswer(String answer) {
                    setAnswer(answer);
                    setStatsLine(DeviceFacts.APP_STATS_LINE);
                    setDetails("");
                    setStatus("Ready");
                    runOnUiThread(() -> askButton.setEnabled(true));
                    busy.set(false);
                }
            });
        } catch (Exception e) {
            setStatus("ERROR: generation failed (" + e.getMessage() + ")");
            runOnUiThread(() -> askButton.setEnabled(true));
            busy.set(false);
        }
    }

    /** AskFlow runner backed by the ExecuTorch LlmModule. */
    private final class LlmRunner implements AskFlow.Runner {
        @Override
        public void resetContext() {
            module.resetContext();
        }

        @Override
        public void generate(String prompt,
                final AskFlow.GenerateCallback flow) {
            LlmGenerationConfig config = LlmGenerationConfig.create()
                    .echo(false)
                    .maxNewTokens(ChatFormat.MAX_NEW_TOKENS)
                    .temperature(ChatFormat.TEMPERATURE)
                    .build();
            module.generate(prompt, config, new LlmCallback() {
                @Override
                public void onResult(String token) {
                    flow.onResult(token);
                }

                @Override
                public void onStats(String stats) {
                    flow.onStats(stats);
                }

                @Override
                public void onError(int code, String message) {
                    flow.onError(code, message);
                }
            });
        }
    }

    @Override
    protected void onDestroy() {
        worker.execute(() -> {
            if (module != null) {
                try {
                    module.close();
                } catch (Exception ignored) {
                    // Best effort on shutdown.
                }
                module = null;
            }
        });
        worker.shutdown();
        super.onDestroy();
    }
}
