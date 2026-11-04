package com.stargate.assistant;

/**
 * Llama 3.2 Instruct chat format and generation settings.
 *
 * <p>Format and token IDs are from the Llama 3.2 1B Instruct tokenizer
 * on disk: {@code /workspaces/m4b-downloads/Llama-3.2-1B-Instruct-Q4_K_M.gguf}
 * metadata keys {@code tokenizer.chat_template} (Meta's Llama 3.2 jinja
 * template), {@code tokenizer.ggml.tokens} (128000
 * {@code <|begin_of_text|>}, 128001 {@code <|end_of_text|>}, 128006
 * {@code <|start_header_id|>}, 128007 {@code <|end_header_id|>}, 128009
 * {@code <|eot_id|>}), {@code tokenizer.ggml.bos_token_id} (128000) and
 * {@code tokenizer.ggml.eos_token_id} (128009). The Pair E
 * {@code tokenizer.model} on disk carries only IDs 0-127999, so it gives
 * neither the template nor the special-token IDs, and the
 * executorch-community model README was never downloaded (not device
 * payload). The template's "Cutting Knowledge Date / Today Date" preamble
 * is deliberately omitted: this assistant must not claim time knowledge
 * (see SYSTEM_MESSAGE), and the template's fallback date would be stale.
 *
 * <p>Sampling: no temperature/top-p defaults are on disk (the GGUF
 * metadata carries none and the model card was never fetched), so
 * TEMPERATURE keeps the previous value 0.8, which is also
 * LlmGenerationConfig.Builder's default.
 */
public final class ChatFormat {

    public static final String BEGIN_OF_TEXT = "<|begin_of_text|>";
    public static final int BEGIN_OF_TEXT_ID = 128000;
    public static final String END_OF_TEXT = "<|end_of_text|>";
    public static final int END_OF_TEXT_ID = 128001;
    public static final String START_HEADER = "<|start_header_id|>";
    public static final int START_HEADER_ID = 128006;
    public static final String END_HEADER = "<|end_header_id|>";
    public static final int END_HEADER_ID = 128007;
    public static final String END_OF_TURN = "<|eot_id|>";
    public static final int END_OF_TURN_ID = 128009;
    public static final String END_OF_MESSAGE = "<|eom_id|>";
    public static final String PYTHON_TAG = "<|python_tag|>";
    public static final String FINETUNE_PAD = "<|finetune_right_pad_id|>";

    /**
     * Known control tokens that are never answer content: stripped from
     * the displayed text. The two stop markers (END_OF_TURN, END_OF_TEXT)
     * are not listed here; display is cut at the first of those instead.
     */
    public static final String[] DISPLAY_ONLY_TOKENS = {
        BEGIN_OF_TEXT, START_HEADER, END_HEADER, END_OF_MESSAGE,
        PYTHON_TAG, FINETUNE_PAD,
    };

    /** Single cap for new tokens per answer. */
    public static final int MAX_NEW_TOKENS = 256;

    /** Kept: no sampling defaults are on disk (see class note). */
    public static final float TEMPERATURE = 0.8f;

    public static final String SYSTEM_MESSAGE =
            "You are an offline assistant on this phone. You cannot see "
            + "the phone's sensors, battery, files, time, or the internet. "
            + "Say so plainly when asked about them. Keep answers short.";

    private ChatFormat() {
    }

    /** Exact prompt string for one user question (golden-tested). */
    public static String buildPrompt(String question) {
        String asked = question == null ? "" : question.trim();
        return BEGIN_OF_TEXT
                + START_HEADER + "system" + END_HEADER + "\n\n"
                + SYSTEM_MESSAGE + END_OF_TURN
                + START_HEADER + "user" + END_HEADER + "\n\n"
                + asked + END_OF_TURN
                + START_HEADER + "assistant" + END_HEADER + "\n\n";
    }

    /** Index of the first stop marker, or -1 when neither is present. */
    public static int stopIndex(String text) {
        if (text == null) {
            return -1;
        }
        int turn = text.indexOf(END_OF_TURN);
        int end = text.indexOf(END_OF_TEXT);
        if (turn < 0) {
            return end;
        }
        if (end < 0) {
            return turn;
        }
        return Math.min(turn, end);
    }

    /**
     * Display text: cut at the first stop marker, with the other known
     * control tokens removed. Never contains control-token text.
     */
    public static String displayText(String raw) {
        if (raw == null) {
            return "";
        }
        int cut = stopIndex(raw);
        String shown = cut >= 0 ? raw.substring(0, cut) : raw;
        for (String token : DISPLAY_ONLY_TOKENS) {
            shown = shown.replace(token, "");
        }
        return shown;
    }

    /**
     * CLI for the host test: {@code ChatFormat cap} prints the token cap;
     * {@code ChatFormat build <question>} prints the exact prompt.
     */
    public static void main(String[] args) {
        if (args.length == 1 && args[0].equals("cap")) {
            System.out.println(MAX_NEW_TOKENS);
            return;
        }
        if (args.length == 2 && args[0].equals("build")) {
            System.out.print(buildPrompt(args[1]));
            return;
        }
        System.out.println("ERROR expected 'cap' or 'build <question>'");
        System.exit(2);
    }
}
