package com.stargate.assistant;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.SocketTimeoutException;
import java.net.URL;
import java.nio.charset.Charset;
import java.util.List;
import java.util.Map;

/**
 * Real HTTPS layer. Redirects are refused (never followed); TLS
 * uses the platform defaults with no custom trust settings, no
 * custom hostname check, and no client configuration of any kind.
 * Success bodies are read up to the 256 KB cap plus one byte so
 * the caller can cut and label; error bodies are read up to
 * 64 KB for status detection only and are never shown.
 *
 * <p>One send owns one budget: connecting may take at most the
 * budget, and reading then gets only the time left, enforced
 * both as the read timeout and by checking the deadline
 * between reads, so a reply that trickles in slowly is
 * stopped at the deadline. The connection is always
 * disconnected before returning or throwing.
 */
public final class HttpNetworkLayer implements NetworkLayer {

    private static final Charset UTF8 = Charset.forName("UTF-8");
    private static final int ERROR_HEAD_BYTES = 65536;

    /** In-flight connection, so cancel() can drop it. */
    private volatile HttpURLConnection current;

    @Override
    public void cancel() {
        HttpURLConnection conn = current;
        if (conn != null) {
            conn.disconnect();
        }
    }

    @Override
    public Response send(CloudRequest.WireRequest request,
            int timeoutMs) throws IOException {
        final long deadlineMs = nowMs() + timeoutMs;
        final CloudAsk.Clock clock = new CloudAsk.Clock() {
            @Override
            public long nowMs() {
                return HttpNetworkLayer.nowMs();
            }
        };
        URL url = new URL("https://" + request.host + request.path);
        HttpURLConnection conn =
                (HttpURLConnection) url.openConnection();
        current = conn;
        try {
            setup(conn, timeoutMs);
            byte[] body = request.body.getBytes(UTF8);
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type",
                    "application/json; charset=utf-8");
            conn.setRequestProperty("Authorization",
                    request.authHeader);
            conn.setDoOutput(true);
            conn.setFixedLengthStreamingMode(body.length);
            OutputStream out = conn.getOutputStream();
            try {
                out.write(body);
            } finally {
                out.close();
            }
            int status = conn.getResponseCode();
            long left = deadlineMs - nowMs();
            if (left <= 0) {
                throw new SocketTimeoutException("timed out");
            }
            conn.setReadTimeout(
                    (int) Math.min(left, Integer.MAX_VALUE));
            int retryAfter = retryAfter(conn.getHeaderFields());
            InputStream in = status >= 200 && status < 300
                    ? conn.getInputStream() : conn.getErrorStream();
            boolean ok = status >= 200 && status < 300;
            int cap = ok ? CloudSpec.REPLY_MAX_BYTES + 1
                    : ERROR_HEAD_BYTES;
            byte[] raw = readUpTo(in, cap, clock, deadlineMs);
            boolean truncated = ok
                    && raw.length > CloudSpec.REPLY_MAX_BYTES;
            int kept = truncated ? CloudSpec.REPLY_MAX_BYTES
                    : raw.length;
            return new Response(status,
                    new String(raw, 0, kept, UTF8), retryAfter,
                    truncated);
        } finally {
            current = null;
            conn.disconnect();
        }
    }

    /** Monotonic milliseconds for the send's own deadline. */
    private static long nowMs() {
        return System.nanoTime() / 1000000L;
    }

    /**
     * Connection setup, separate so the host test proves on a fake
     * connection that redirects are refused and timeouts are set.
     */
    static void setup(HttpURLConnection conn, int timeoutMs) {
        conn.setInstanceFollowRedirects(false);
        conn.setConnectTimeout(timeoutMs);
        conn.setReadTimeout(timeoutMs);
    }

    /** Retry-After in seconds, or -1 when absent or unparseable. */
    static int retryAfter(Map<String, List<String>> headers) {
        if (headers == null) {
            return -1;
        }
        for (Map.Entry<String, List<String>> entry
                : headers.entrySet()) {
            if (entry.getKey() == null
                    || !entry.getKey().equalsIgnoreCase(
                            "Retry-After")) {
                continue;
            }
            List<String> values = entry.getValue();
            if (values == null || values.isEmpty()) {
                return -1;
            }
            try {
                return Integer.parseInt(values.get(0).trim());
            } catch (NumberFormatException bad) {
                return -1;
            }
        }
        return -1;
    }

    /**
     * Reads up to cap bytes, stopping at the deadline: a read
     * timeout alone only waits for the next bytes, so the
     * monotonic clock is checked before every read and the
     * half-read body is dropped with a timeout when time runs
     * out. The stream is always closed.
     */
    static byte[] readUpTo(InputStream in, int cap,
            CloudAsk.Clock clock, long deadlineMs)
            throws IOException {
        if (in == null) {
            return new byte[0];
        }
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        try {
            while (out.size() < cap) {
                if (clock.nowMs() >= deadlineMs) {
                    throw new SocketTimeoutException(
                            "timed out");
                }
                int room = Math.min(buf.length, cap - out.size());
                int got = in.read(buf, 0, room);
                if (got < 0) {
                    break;
                }
                out.write(buf, 0, got);
            }
        } finally {
            in.close();
        }
        return out.toByteArray();
    }
}
