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
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Real HTTPS layer. Redirects are refused (never followed); TLS
 * uses the platform defaults with no custom trust settings, no
 * custom hostname check, and no client configuration of any kind.
 * Success bodies are read up to the 256 KB cap plus one byte so
 * the caller can cut and label; error bodies are read up to
 * 64 KB for status detection only and are never shown.
 *
 * <p>One send owns one budget, and the budget is absolute:
 * each read is given only the time left, the request body is
 * written in small chunks with the deadline checked before
 * each chunk, a scheduled close fires at the deadline, and
 * the blocking I/O runs on a worker while the caller waits
 * only until the deadline, so the question stops at the
 * deadline even if the socket itself does not unblock at
 * once (closing a {@code HttpURLConnection} stream from
 * another thread need not interrupt a blocked read on every
 * JDK, and cancelling a {@code Future} only attempts
 * interruption). After the deadline the half-read body is
 * dropped with a timeout. Closing a {@code HttpURLConnection}
 * stream from another thread can stall the closer until the
 * blocked read ends instead of interrupting it, so every
 * caller-side close and disconnect runs on a daemon closer
 * thread, never on the caller: the caller returns at the
 * deadline even when the socket itself stays blocked.
 *
 * <p>A worker that is still blocked when its caller stops
 * waiting keeps its socket until its own I/O actually ends.
 * Transport admission stays closed until that worker has
 * exited: a retry or the next question is refused with
 * "previous request still closing" instead of overlapping
 * it. The timed-out caller itself always returns at the
 * deadline; it never waits for the worker.
 */
public final class HttpNetworkLayer implements NetworkLayer {

    private static final Charset UTF8 = Charset.forName("UTF-8");
    private static final int ERROR_HEAD_BYTES = 65536;

    /**
     * Request-body write chunk: the deadline is checked
     * before every chunk, so a slow write stops between
     * chunks once the budget is gone.
     */
    private static final int WRITE_CHUNK_BYTES = 2048;

    /**
     * Refusal while an earlier send's worker still holds
     * its socket after its caller already stopped waiting.
     * A retry or the next question gets this instead of
     * overlapping the worker.
     */
    static final String STILL_CLOSING =
            "previous request still closing";

    /**
     * Workers that outlived their caller's wait and still
     * run. Raised only when the caller stops waiting while
     * its worker may still be alive, lowered exactly once
     * when that worker actually exits.
     */
    private static final AtomicInteger CLOSING =
            new AtomicInteger(0);

    /** Single daemon thread that fires send deadlines. */
    private static final ScheduledExecutorService DEADLINE =
            Executors.newScheduledThreadPool(1,
                    new ThreadFactory() {
                        @Override
                        public Thread newThread(Runnable task) {
                            Thread thread = new Thread(task,
                                    "m4c-deadline");
                            thread.setDaemon(true);
                            return thread;
                        }
                    });

    /** Daemon workers that run the blocking send I/O. */
    private static final ExecutorService IO =
            Executors.newCachedThreadPool(
                    new ThreadFactory() {
                        @Override
                        public Thread newThread(Runnable task) {
                            Thread thread = new Thread(task,
                                    "m4c-io");
                            thread.setDaemon(true);
                            return thread;
                        }
                    });

    /**
     * Daemon threads that run the blocking closes. Closing a
     * {@code HttpURLConnection} stream from another thread can
     * stall the closer until the blocked read ends, so the
     * caller and the deadline thread never close synchronously.
     */
    private static final ExecutorService CLOSER =
            Executors.newCachedThreadPool(
                    new ThreadFactory() {
                        @Override
                        public Thread newThread(Runnable task) {
                            Thread thread = new Thread(task,
                                    "m4c-closer");
                            thread.setDaemon(true);
                            return thread;
                        }
                    });

    /** In-flight connection, so cancel() can drop it. */
    private volatile HttpURLConnection current;

    /** In-flight send worker, so cancel() can drop it. */
    private volatile Future<?> currentTask;

    /**
     * Single-thread guard: at most one send runs on one
     * instance at a time, so concurrent sends can never
     * clobber each other's cancel state.
     */
    private final AtomicBoolean busy = new AtomicBoolean(false);

    @Override
    public void cancel() {
        Future<?> task = currentTask;
        if (task != null) {
            task.cancel(true);
        }
        HttpURLConnection conn = current;
        if (conn != null) {
            closeAsync(conn, null, null);
        }
    }

    /**
     * Fires the deadline: closes the per-send request and
     * response streams when known and disconnects the
     * per-send connection. Best-effort and safe to repeat.
     * Runs only on a closer thread (see {@link #closeAsync}):
     * a blocked {@code HttpURLConnection} read or write
     * need not return at once, and the close itself can
     * stall until it does, so the caller stops waiting at
     * the deadline instead and admission stays closed until
     * the worker has actually exited (see {@link #sendTo}).
     */
    static void fireDeadline(HttpURLConnection conn,
            InputStream in, OutputStream out) {
        if (out != null) {
            try {
                out.close();
            } catch (IOException ignored) {
            }
        }
        if (in != null) {
            try {
                in.close();
            } catch (IOException ignored) {
            }
        }
        if (conn != null) {
            conn.disconnect();
        }
    }

    /**
     * Runs a deadline close off the caller: returns at once
     * while a daemon closer thread does the blocking close.
     */
    static void closeAsync(final HttpURLConnection conn,
            final InputStream in, final OutputStream out) {
        CLOSER.execute(new Runnable() {
            @Override
            public void run() {
                fireDeadline(conn, in, out);
            }
        });
    }

    @Override
    public Response send(CloudRequest.WireRequest request,
            int timeoutMs) throws IOException {
        URL url = new URL("https://" + request.host + request.path);
        return sendTo(request, timeoutMs, url);
    }

    /**
     * Sends to an explicit URL: production always passes the
     * provider's https URL, and the host test passes a local
     * http URL so the same code runs against a stalling fake
     * server on the loopback interface.
     *
     * <p>Single-threaded per instance: at most one {@code send}
     * or {@code sendTo} may run on one layer at a time;
     * concurrent use throws {@code IllegalStateException}.
     * Production never shares an instance between questions
     * (one question runs at a time), and a retry or the next
     * question runs only after the previous send returned.
     *
     * <p>The blocking I/O runs on a daemon worker while this
     * thread waits only until the deadline: at the deadline it
     * hands the per-send close to a daemon closer, drops the
     * worker's late result, and throws the deadline outcome,
     * so the question stops even when the socket itself stays
     * blocked (the close itself can stall until a blocked
     * read ends, so the caller never runs it synchronously).
     * The per-send timer and worker belong only to this send
     * and are cancelled before returning. When the worker is
     * still alive after its caller stopped waiting, admission
     * stays closed until it has actually exited: the next send
     * is refused with "previous request still closing".
     */
    Response sendTo(final CloudRequest.WireRequest request,
            final int timeoutMs, URL url) throws IOException {
        return sendBody(request.body, request.authHeader,
                timeoutMs, url);
    }

    /**
     * Test seam: the same machinery with an explicit body, so
     * a stalled request-body write is observable against a
     * loopback server that never reads. Production bodies stay
     * under the 16 KB cap, which can still stall on a real
     * network; the seam only makes the stall deterministic.
     */
    Response sendBody(final String bodyText,
            final String authHeader, final int timeoutMs,
            URL url) throws IOException {
        if (!busy.compareAndSet(false, true)) {
            throw new IllegalStateException(
                    "sendTo is single-threaded per instance");
        }
        try {
            if (CLOSING.get() > 0) {
                throw new IOException(STILL_CLOSING);
            }
            final long deadlineMs = nowMs() + timeoutMs;
            final CloudAsk.Clock clock = new CloudAsk.Clock() {
                @Override
                public long nowMs() {
                    return HttpNetworkLayer.nowMs();
                }
            };
            final HttpURLConnection conn =
                    (HttpURLConnection) url.openConnection();
            current = conn;
            final AtomicReference<InputStream> inBox =
                    new AtomicReference<InputStream>();
            final AtomicReference<OutputStream> outBox =
                    new AtomicReference<OutputStream>();
            final AtomicBoolean started =
                    new AtomicBoolean(false);
            final AtomicBoolean exited =
                    new AtomicBoolean(false);
            final AtomicBoolean closing =
                    new AtomicBoolean(false);
            ScheduledFuture<?> killer = DEADLINE.schedule(
                    new Runnable() {
                        @Override
                        public void run() {
                            closeAsync(conn, inBox.get(),
                                    outBox.get());
                        }
                    },
                    Math.max(deadlineMs - nowMs(), 0L),
                    TimeUnit.MILLISECONDS);
            Future<Response> task = null;
            boolean observedDone = false;
            try {
                task = IO.submit(
                        new Callable<Response>() {
                            @Override
                            public Response call()
                                    throws Exception {
                                started.set(true);
                                try {
                                    return doSend(bodyText,
                                            authHeader,
                                            timeoutMs, conn,
                                            deadlineMs, clock,
                                            inBox, outBox);
                                } finally {
                                    exited.set(true);
                                    if (closing.getAndSet(
                                            false)) {
                                        CLOSING.decrementAndGet();
                                    }
                                }
                            }
                        });
                currentTask = task;
                long remaining = deadlineMs - nowMs();
                if (remaining <= 0) {
                    throw new SocketTimeoutException(
                            "timed out");
                }
                try {
                    Response done = task.get(remaining,
                            TimeUnit.MILLISECONDS);
                    observedDone = true;
                    return done;
                } catch (TimeoutException timedOut) {
                    throw new SocketTimeoutException(
                            "timed out");
                } catch (ExecutionException failed) {
                    observedDone = true;
                    Throwable cause = failed.getCause();
                    if (cause instanceof IOException) {
                        throw (IOException) cause;
                    }
                    if (cause instanceof RuntimeException) {
                        throw (RuntimeException) cause;
                    }
                    throw new IOException(cause);
                } catch (InterruptedException stopped) {
                    Thread.currentThread().interrupt();
                    throw new IOException(stopped);
                }
            } finally {
                if (task != null) {
                    task.cancel(true);
                }
                if (!observedDone && task != null
                        && !exited.get()) {
                    CLOSING.incrementAndGet();
                    closing.set(true);
                    if ((exited.get() || !started.get())
                            && closing.getAndSet(false)) {
                        CLOSING.decrementAndGet();
                    }
                }
                if (!observedDone) {
                    closeAsync(conn, inBox.get(),
                            outBox.get());
                }
                killer.cancel(false);
                currentTask = null;
                current = null;
            }
        } finally {
            busy.set(false);
        }
    }

    /**
     * Worker body for {@link #sendTo}: connects, writes, reads
     * the status and the capped body. Each read gets only the
     * time left and the write is chunked against the deadline;
     * the streams are published to the per-send boxes so the
     * deadline close finds them. Always disconnects, so the
     * server sees the close once the worker leaves its
     * blocked call.
     */
    private Response doSend(String bodyText, String authHeader,
            int timeoutMs, HttpURLConnection conn,
            long deadlineMs, CloudAsk.Clock clock,
            AtomicReference<InputStream> inBox,
            AtomicReference<OutputStream> outBox)
            throws IOException {
        try {
            setup(conn, timeoutMs);
            byte[] body = bodyText.getBytes(UTF8);
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type",
                    "application/json; charset=utf-8");
            conn.setRequestProperty("Authorization",
                    authHeader);
            conn.setDoOutput(true);
            conn.setFixedLengthStreamingMode(body.length);
            OutputStream out = conn.getOutputStream();
            outBox.set(out);
            try {
                writeBody(out, body, deadlineMs);
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
            inBox.set(in);
            boolean ok = status >= 200 && status < 300;
            int cap = ok ? CloudSpec.REPLY_MAX_BYTES + 1
                    : ERROR_HEAD_BYTES;
            byte[] raw = readUpTo(conn, in, cap, clock,
                    deadlineMs);
            boolean truncated = ok
                    && raw.length > CloudSpec.REPLY_MAX_BYTES;
            int kept = truncated ? CloudSpec.REPLY_MAX_BYTES
                    : raw.length;
            return new Response(status,
                    new String(raw, 0, kept, UTF8), retryAfter,
                    truncated);
        } finally {
            conn.disconnect();
        }
    }

    /**
     * Writes the request body in small chunks, checking the
     * deadline before each chunk and stopping with a timeout
     * once the budget is gone. A chunk already handed to the
     * socket has no timeout of its own; the caller stops
     * waiting for it via the worker's deadline, and admission
     * stays closed until the worker has actually exited.
     */
    private static void writeBody(OutputStream out,
            byte[] body, long deadlineMs) throws IOException {
        int off = 0;
        while (off < body.length) {
            if (deadlineMs - nowMs() <= 0) {
                throw new SocketTimeoutException("timed out");
            }
            int n = Math.min(WRITE_CHUNK_BYTES,
                    body.length - off);
            out.write(body, off, n);
            off += n;
        }
    }

    /**
     * Test-only: workers still closing their socket after
     * their caller already stopped waiting.
     */
    static int closingCount() {
        return CLOSING.get();
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
     * Reads up to cap bytes, stopping at the deadline. Before
     * every read the socket read timeout is set to the time
     * left (at least 1 ms; nothing left stops with a
     * timeout), since a read timeout only waits for the next
     * bytes; the scheduled disconnect fires at the deadline
     * as well, but a read that is already blocked need not
     * return at once (closing a {@code HttpURLConnection}
     * stream from another thread need not interrupt a
     * blocked read). The caller stops waiting at the deadline
     * by dropping the worker instead, and no new send is
     * admitted until the worker has actually exited. The
     * half-read body is dropped with a timeout when time runs
     * out, and the stream is always closed.
     */
    static byte[] readUpTo(HttpURLConnection conn,
            InputStream in, int cap,
            CloudAsk.Clock clock, long deadlineMs)
            throws IOException {
        if (in == null) {
            return new byte[0];
        }
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        try {
            while (out.size() < cap) {
                long left = deadlineMs - clock.nowMs();
                if (left <= 0) {
                    throw new SocketTimeoutException(
                            "timed out");
                }
                conn.setReadTimeout((int) Math.min(
                        Math.max(left, 1L),
                        Integer.MAX_VALUE));
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
