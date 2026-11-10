package com.stargate.hub;

import android.content.Context;
import android.os.SystemClock;

import com.stargate.assistant.ChatFormat;
import com.stargate.assistant.CloudAsk;
import com.stargate.assistant.CloudSpec;
import com.stargate.assistant.HttpNetworkLayer;
import com.stargate.assistant.KeyStorage;
import com.stargate.assistant.KeystoreKeyStorage;

import java.io.File;
import java.util.Map;

/**
 * The assistant's two engines on a real phone: the cloud provider
 * (the person's own key, kept in the Android Keystore) and, when this
 * build carries one, the model that runs on the phone itself.
 */
final class AndroidAsk implements AskBackend {
    /** The on-phone engine; only present in builds that carry a model. */
    interface Local {
        String answer(String question) throws Exception;
    }

    private static final String PROVIDER = CloudSpec.BASETEN;
    private final KeyStorage keys;
    private final String localName;
    private final Local local;

    AndroidAsk(Context app, Map<String, Object> config) {
        keys = new KeystoreKeyStorage(app);
        String name = null;
        Local engine = null;
        Object model = config.get("local_model");
        if (model instanceof Map) {
            @SuppressWarnings("unchecked")
            Map<String, Object> spec = (Map<String, Object>) model;
            try {
                // Loaded by name so a build without a model does not
                // carry the model runtime at all.
                engine = (Local) Class.forName(
                        "com.stargate.hub.LocalModel")
                        .getConstructor(File.class, Map.class)
                        .newInstance(app.getExternalFilesDir(null), spec);
                name = Json.str(spec, "name");
            } catch (ReflectiveOperationException | RuntimeException
                    | LinkageError absent) {
                engine = null;
            }
        }
        local = engine;
        localName = engine == null ? null : name;
    }

    @Override
    public String localName() {
        return localName;
    }

    @Override
    public String cloudName() {
        return "Muse Spark Open, hosted on Baseten";
    }

    @Override
    public boolean keySet() {
        return !keys.lastFour(PROVIDER).isEmpty();
    }

    @Override
    public void saveKey(String key) throws Exception {
        keys.save(PROVIDER, key);
    }

    @Override
    public void deleteKey() {
        keys.delete(PROVIDER);
    }

    @Override
    public Answer ask(String mode, String question) {
        if ("local".equals(mode)) {
            if (local == null) {
                return new Answer(false,
                        "This phone has no model of its own.");
            }
            try {
                return new Answer(true, local.answer(question));
            } catch (Exception failed) {
                return new Answer(false, "The model on the phone could "
                        + "not answer (" + failed.getMessage() + ").");
            }
        }
        final Answer[] result = new Answer[1];
        CloudAsk.ask(PROVIDER, CloudSpec.OPEN_WEIGHT_MODEL,
                ChatFormat.SYSTEM_MESSAGE, question, keys,
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
                    public void onAnswer(String text, boolean incomplete,
                            boolean truncated, long promptTokens,
                            long completionTokens) {
                        result[0] = new Answer(true, incomplete
                                ? text + CloudSpec.INCOMPLETE_LABEL : text);
                    }

                    @Override
                    public void onError(String userMessage) {
                        result[0] = new Answer(false, userMessage);
                    }

                    @Override
                    public void onRefused(String userMessage) {
                        result[0] = new Answer(false, userMessage);
                    }
                });
        return result[0] != null ? result[0]
                : new Answer(false, "No answer came back.");
    }
}
