package com.stargate.hub;

import com.stargate.assistant.AnswerAccumulator;
import com.stargate.assistant.ChatFormat;

import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.security.MessageDigest;
import java.util.Map;

import org.pytorch.executorch.extension.llm.LlmCallback;
import org.pytorch.executorch.extension.llm.LlmGenerationConfig;
import org.pytorch.executorch.extension.llm.LlmModule;

/**
 * The model that runs on the phone itself. Only compiled into builds
 * that carry a model, so other builds do not ship the model runtime.
 * The model files are checked against the hashes baked in at build
 * time before they are ever loaded; it loads on the first question.
 */
final class LocalModel implements AndroidAsk.Local {
    private final File pte;
    private final File tok;
    private final String pteSha;
    private final String tokSha;
    private LlmModule module;

    LocalModel(File filesDir, Map<String, Object> spec) {
        String dir = Json.str(spec, "dir");
        File base = dir != null ? new File(dir)
                : new File(filesDir, "model");
        pte = new File(base, Json.str(spec, "pte"));
        tok = new File(base, Json.str(spec, "tokenizer"));
        pteSha = Json.str(spec, "pte_sha256");
        tokSha = Json.str(spec, "tokenizer_sha256");
        if (pteSha == null || tokSha == null) {
            throw new IllegalArgumentException("model hashes missing");
        }
    }

    private static String sha256(File file) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (InputStream in = new FileInputStream(file)) {
            byte[] buf = new byte[1 << 16];
            int got;
            while ((got = in.read(buf)) >= 0) {
                digest.update(buf, 0, got);
            }
        }
        StringBuilder hex = new StringBuilder();
        for (byte b : digest.digest()) {
            hex.append(String.format("%02x", b & 0xff));
        }
        return hex.toString();
    }

    private void load() throws Exception {
        if (module != null) {
            return;
        }
        if (!pte.isFile() || !tok.isFile()) {
            throw new IllegalStateException("model files are missing");
        }
        if (!pteSha.equals(sha256(pte)) || !tokSha.equals(sha256(tok))) {
            throw new IllegalStateException(
                    "model files do not match this build");
        }
        LlmModule loaded = new LlmModule(LlmModule.MODEL_TYPE_TEXT,
                pte.getAbsolutePath(), tok.getAbsolutePath(),
                ChatFormat.TEMPERATURE);
        loaded.load();
        module = loaded;
    }

    @Override
    public synchronized String answer(String question) throws Exception {
        load();
        final AnswerAccumulator acc = new AnswerAccumulator();
        final String[] error = new String[1];
        module.resetContext();
        LlmGenerationConfig config = LlmGenerationConfig.create()
                .echo(false)
                .maxNewTokens(ChatFormat.MAX_NEW_TOKENS)
                .temperature(ChatFormat.TEMPERATURE)
                .build();
        module.generate(ChatFormat.buildPrompt(question), config,
                new LlmCallback() {
                    @Override
                    public void onResult(String token) {
                        if (acc.append(token)) {
                            module.stop();
                        }
                    }

                    @Override
                    public void onStats(String stats) {
                        // Not shown.
                    }

                    @Override
                    public void onError(int code, String message) {
                        error[0] = message;
                    }
                });
        if (error[0] != null) {
            throw new IllegalStateException(error[0]);
        }
        return acc.display().trim();
    }
}
