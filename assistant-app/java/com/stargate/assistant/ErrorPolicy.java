package com.stargate.assistant;

/**
 * Section 3 error table (pure Java). Every decision shows a short
 * message with the provider and never the response body or
 * headers; no decision ever switches model or provider (a retry
 * reuses the same request). Hard quota-exhausted errors never
 * retry, even on 429.
 */
public final class ErrorPolicy {

    /** One decision: answer, retry once, or fail. */
    public static final class Decision {
        public final boolean answer;
        public final boolean retry;
        public final String message;
        public final int waitSec;

        private Decision(boolean answer, boolean retry,
                String message, int waitSec) {
            this.answer = answer;
            this.retry = retry;
            this.message = message;
            this.waitSec = waitSec;
        }
    }

    private ErrorPolicy() {
    }

    /**
     * Decides from the status, the reply parsed from the body, and
     * the Retry-After seconds (-1 when absent). The body itself is
     * only inspected for the model name and billing/quota words;
     * it is never shown.
     */
    public static Decision decide(String provider, String model,
            int status, String body, Json.Reply reply,
            int retryAfterSec) {
        String shown = CloudSpec.display(provider);
        if (status >= 200 && status < 300) {
            if (!reply.ok || reply.text.isEmpty()) {
                return fail("No usable answer");
            }
            if (reply.finishReason != null
                    && !"stop".equals(reply.finishReason)) {
                return answer(reply.text
                        + CloudSpec.INCOMPLETE_LABEL);
            }
            return answer(reply.text);
        }
        if (status == 401 || status == 403) {
            return fail("Key rejected by " + shown);
        }
        if (status == 402 || billing(body) || quota(body)) {
            return fail(shown + " account needs payment");
        }
        if ((status == 400 || status == 404)
                && body != null && model != null
                && body.contains(model)) {
            return fail("Model not available");
        }
        if (status == 429) {
            if (retryAfterSec > CloudSpec.RETRY_AFTER_MAX_S) {
                return fail("Too many requests, try later");
            }
            int wait = retryAfterSec >= 0 ? retryAfterSec
                    : CloudSpec.RETRY_AFTER_DEFAULT_S;
            return retry("Too many requests, try later", wait);
        }
        if (status >= 500) {
            return retry(shown + " not reachable", 0);
        }
        return fail(shown + " not reachable");
    }

    private static boolean billing(String body) {
        if (body == null) {
            return false;
        }
        String low = body.toLowerCase();
        return low.contains("billing") || low.contains("payment")
                || low.contains("insufficient")
                || low.contains("invoice");
    }

    private static boolean quota(String body) {
        if (body == null) {
            return false;
        }
        return body.toLowerCase().contains("quota");
    }

    private static Decision answer(String text) {
        return new Decision(true, false, text, 0);
    }

    private static Decision fail(String message) {
        return new Decision(false, false, message, 0);
    }

    private static Decision retry(String message, int waitSec) {
        return new Decision(false, true, message, waitSec);
    }
}
