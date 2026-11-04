package com.stargate.assistant;

/**
 * One-question flow (pure Java, so the host test runs this exact class
 * on a JVM with a fake runner).
 *
 * <p>Two guarantees, both tested: (a) device-fact questions are
 * answered by the app and never touch the runner; (b) every other
 * question resets the runner first, so each question starts from an
 * empty context instead of carrying the previous turns' KV cache
 * (the Mac log showed positions climbing 60, 134, 236 ... 928).
 * MainActivity backs this with the real ExecuTorch LlmModule.
 */
public final class AskFlow {

    /** Minimal runner surface: reset plus one generate call. */
    public interface Runner {
        void resetContext();

        void generate(String prompt, GenerateCallback callback);
    }

    /** One-question callbacks, including the app-side answer. */
    public interface GenerateCallback {
        void onResult(String token);

        void onStats(String stats);

        void onError(int code, String message);

        void onAppAnswer(String answer);
    }

    private AskFlow() {
    }

    /**
     * Runs one question. Returns true when the app answered itself
     * (the runner untouched); otherwise the runner is reset and then
     * asked once with the chat-format prompt.
     */
    public static boolean ask(Runner runner, String question,
            GenerateCallback callback) {
        if (DeviceFacts.isDeviceFactQuestion(question)) {
            callback.onAppAnswer(DeviceFacts.ANSWER);
            return true;
        }
        runner.resetContext();
        runner.generate(ChatFormat.buildPrompt(question), callback);
        return false;
    }
}
