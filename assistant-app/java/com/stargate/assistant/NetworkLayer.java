package com.stargate.assistant;

import java.io.IOException;

/**
 * Network seam (pure Java): the real layer sends over HTTPS, and
 * host tests swap in a fake that counts requests and replays
 * canned replies without any network.
 */
public interface NetworkLayer {

    /** One HTTP reply. Absent Retry-After is -1. */
    final class Response {
        public final int status;
        public final String body;
        public final int retryAfterSec;
        public final boolean truncated;

        public Response(int status, String body, int retryAfterSec,
                boolean truncated) {
            this.status = status;
            this.body = body == null ? "" : body;
            this.retryAfterSec = retryAfterSec;
            this.truncated = truncated;
        }
    }

    /**
     * Sends one request. Timeouts and refused redirects surface as
     * {@link IOException}; HTTP statuses come back as responses.
     */
    Response send(CloudRequest.WireRequest request, int timeoutMs)
            throws IOException;

    /**
     * Cancels and closes the in-flight request, if any. Called
     * on the question's timeout path, before the busy slot is
     * released, so a stuck transport never keeps running into
     * the next question. The default is a no-op for layers
     * with nothing to cancel.
     */
    default void cancel() {
    }
}
