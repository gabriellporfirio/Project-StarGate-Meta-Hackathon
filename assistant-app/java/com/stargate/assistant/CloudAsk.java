package com.stargate.assistant;

import java.io.IOException;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * One cloud question (pure Java over the seams, so host tests run
 * it with fakes). Refusals (no key, bad model ID, over the caps)
 * happen before the network layer sees anything; one send plus at
 * most one retry share a single 60 s deadline including any wait,
 * and a retry reuses the same request, never another model or
 * provider. Each send and each wait gets only the time left; a
 * reply that arrives after the deadline is dropped, and when
 * time runs out the transport is cancelled and closed and the
 * error is "timed out". A second question while one runs is
 * refused.
 */
public final class CloudAsk {

    /** One-question callbacks. Usage -1 means unreported. */
    public interface Callback {
        void onAnswer(String text, boolean incomplete,
                boolean truncated, long promptTokens,
                long completionTokens);

        void onError(String userMessage);

        void onRefused(String userMessage);
    }

    public interface Clock {
        long nowMs();
    }

    public interface Sleeper {
        void sleepMs(long ms) throws InterruptedException;
    }

    private static final AtomicBoolean IN_FLIGHT =
            new AtomicBoolean(false);

    private CloudAsk() {
    }

    /** Test-only reset for the one-at-a-time guard. */
    static void resetForTest() {
        IN_FLIGHT.set(false);
    }

    /**
     * Asks one question. The caller has already handled device-fact
     * questions; only model questions reach here.
     */
    public static void ask(String provider, String model,
            String system, String question, KeyStorage keys,
            NetworkLayer net, Clock clock, Sleeper sleeper,
            Callback callback) {
        if (!IN_FLIGHT.compareAndSet(false, true)) {
            callback.onRefused("Another question is running.");
            return;
        }
        try {
            askLocked(provider, model, system, question, keys,
                    net, clock, sleeper, callback);
        } finally {
            IN_FLIGHT.set(false);
        }
    }

    private static void askLocked(String provider, String model,
            String system, String question, KeyStorage keys,
            NetworkLayer net, Clock clock, Sleeper sleeper,
            Callback callback) {
        if (!CloudSpec.META.equals(provider)
                && !CloudSpec.BASETEN.equals(provider)) {
            callback.onRefused("Unknown provider");
            return;
        }
        String shown = CloudSpec.display(provider);
        String key = keys.load(provider);
        if (key == null || key.isEmpty()) {
            callback.onRefused(
                    "Add your " + shown + " key in settings first.");
            return;
        }
        CloudRequest.WireRequest request;
        try {
            request = CloudRequest.build(provider, key, model,
                    system, question,
                    CloudSpec.OUTPUT_MAX_TOKENS);
        } catch (CloudRequest.Refused refused) {
            if ("model".equals(refused.code)) {
                callback.onRefused("Model not available");
            } else {
                callback.onRefused("Question too long to send.");
            }
            return;
        }
        long deadline = clock.nowMs() + CloudSpec.TIMEOUT_MS;
        ErrorPolicy.Decision last = null;
        for (int attempt = 0; attempt < 2; attempt++) {
            long remaining = deadline - clock.nowMs();
            if (remaining <= 0 && attempt > 0) {
                net.cancel();
                callback.onError("timed out");
                return;
            }
            int timeout = (int) Math.max(1L,
                    Math.min(remaining, CloudSpec.TIMEOUT_MS));
            NetworkLayer.Response done;
            try {
                done = net.send(request, timeout);
            } catch (IOException failed) {
                if (clock.nowMs() >= deadline) {
                    net.cancel();
                    callback.onError("timed out");
                    return;
                }
                last = null;
                if (attempt == 0) {
                    continue;
                }
                callback.onError(shown + " not reachable");
                return;
            }
            if (clock.nowMs() >= deadline) {
                net.cancel();
                callback.onError("timed out");
                return;
            }
            Json.Reply reply = Json.parseReply(done.body);
            ErrorPolicy.Decision decision = ErrorPolicy.decide(
                    provider, model, done.status, done.body, reply,
                    done.retryAfterSec);
            if (decision.answer) {
                String text = decision.message;
                if (done.truncated) {
                    text += CloudSpec.TRUNCATED_LABEL;
                }
                boolean incomplete = reply.finishReason != null
                        && !"stop".equals(reply.finishReason);
                callback.onAnswer(text, incomplete, done.truncated,
                        reply.promptTokens,
                        reply.completionTokens);
                return;
            }
            last = decision;
            if (!decision.retry || attempt > 0) {
                callback.onError(decision.message);
                return;
            }
            try {
                long left = deadline - clock.nowMs();
                long wait = decision.waitSec * 1000L;
                sleeper.sleepMs(
                        Math.min(wait, Math.max(0L, left)));
            } catch (InterruptedException stopped) {
                callback.onError(shown + " not reachable");
                return;
            }
        }
        callback.onError(last == null ? shown + " not reachable"
                : last.message);
    }
}
