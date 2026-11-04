package com.stargate.files;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.SocketException;
import java.nio.charset.StandardCharsets;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;

/**
 * Loopback-only file server inside the Files app's own storage (M4 3a).
 *
 * <p>Plain Java with no Android dependencies, so the host tests compile
 * and run this exact file. Rules, all enforced here:
 * <ul>
 * <li>Listens on 127.0.0.1 only, never on all addresses.
 * <li>A random access code is made on first start (persisted in
 * {@code codeFile}) and every request must carry it as {@code ?code=}.
 * No code or a wrong code is refused with 403.
 * <li>File names stay inside the upload folder: {@code ..}, absolute
 * paths, slashes/backslashes at any encoding level, and links
 * pointing outside are refused.
 * <li>One upload is at most 50 MB and the folder holds at most 200 MB
 * (defaults; the test harness may override them). Anything larger is
 * refused before it is written, and a refused upload leaves no
 * partial file.
 * <li>No outgoing connections: this class only accepts on its
 * ServerSocket and never opens a client socket.
 * </ul>
 */
public class FilesServer {
    /** Bind address, always loopback. */
    public static final String BIND_ADDRESS = "127.0.0.1";
    /** Default per-upload cap: 50 MB. */
    public static final long DEFAULT_MAX_UPLOAD_BYTES = 50L * 1024 * 1024;
    /** Default folder cap: 200 MB. */
    public static final long DEFAULT_MAX_TOTAL_BYTES = 200L * 1024 * 1024;

    private static final int MAX_HEADER_BYTES = 64 * 1024;
    private static final int CODE_LENGTH = 12;
    private static final String CODE_ALPHABET =
            "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
    private static final int MAX_NAME_LENGTH = 255;

    private final Path uploadDir;
    private final File codeFile;
    private final int port;
    private final long maxUploadBytes;
    private final long maxTotalBytes;
    private final Object writeLock = new Object();

    private volatile ServerSocket serverSocket;
    private volatile Thread acceptThread;
    private volatile boolean running;
    private volatile String code;

    public FilesServer(File uploadDir, File codeFile, int port,
                       long maxUploadBytes, long maxTotalBytes) {
        this.uploadDir = uploadDir.toPath().toAbsolutePath().normalize();
        this.codeFile = codeFile;
        this.port = port;
        this.maxUploadBytes = maxUploadBytes;
        this.maxTotalBytes = maxTotalBytes;
    }

    /** Load the persisted code, or make a random one on first start. */
    public String ensureCode() throws IOException {
        if (code != null) {
            return code;
        }
        synchronized (this) {
            if (code != null) {
                return code;
            }
            if (codeFile.isFile()) {
                String stored = new String(
                        Files.readAllBytes(codeFile.toPath()),
                        StandardCharsets.UTF_8).trim();
                if (!stored.isEmpty()) {
                    code = stored;
                    return code;
                }
            }
            SecureRandom random = new SecureRandom();
            StringBuilder made = new StringBuilder(CODE_LENGTH);
            for (int i = 0; i < CODE_LENGTH; i++) {
                made.append(CODE_ALPHABET.charAt(
                        random.nextInt(CODE_ALPHABET.length())));
            }
            code = made.toString();
            File parent = codeFile.getParentFile();
            if (parent != null) {
                parent.mkdirs();
            }
            Files.write(codeFile.toPath(),
                    code.getBytes(StandardCharsets.UTF_8));
            return code;
        }
    }

    /** Bind 127.0.0.1 and start accepting (idempotent). */
    public synchronized void start() throws IOException {
        if (running) {
            return;
        }
        Files.createDirectories(uploadDir);
        ensureCode();
        ServerSocket bound = new ServerSocket(
                port, 50, InetAddress.getByName(BIND_ADDRESS));
        serverSocket = bound;
        running = true;
        acceptThread = new Thread(new Runnable() {
            @Override
            public void run() {
                acceptLoop();
            }
        }, "files-accept");
        acceptThread.setDaemon(true);
        acceptThread.start();
    }

    /** Stop accepting and close the listening socket. */
    public synchronized void stop() throws IOException {
        running = false;
        if (serverSocket != null) {
            serverSocket.close();
        }
        if (acceptThread != null) {
            try {
                acceptThread.join(2000);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
    }

    public boolean isRunning() {
        return running;
    }

    /** Actual bound port (useful when constructed with port 0). */
    public int getPort() {
        ServerSocket socket = serverSocket;
        return socket == null ? -1 : socket.getLocalPort();
    }

    /** Actual bound address of the listening socket. */
    public String getBindAddress() {
        ServerSocket socket = serverSocket;
        if (socket == null) {
            return "";
        }
        return socket.getInetAddress().getHostAddress();
    }

    public String getCode() {
        return code;
    }

    public Path getUploadDir() {
        return uploadDir;
    }

    /** Sum of the regular files directly in the upload folder. */
    public long dirTotalBytes() throws IOException {
        long total = 0;
        try (DirectoryStream<Path> stream =
                Files.newDirectoryStream(uploadDir)) {
            for (Path entry : stream) {
                if (Files.isRegularFile(entry)) {
                    total += Files.size(entry);
                }
            }
        }
        return total;
    }

    private void acceptLoop() {
        while (running) {
            try {
                final Socket socket = serverSocket.accept();
                Thread worker = new Thread(new Runnable() {
                    @Override
                    public void run() {
                        handle(socket);
                    }
                }, "files-worker");
                worker.setDaemon(true);
                worker.start();
            } catch (SocketException done) {
                break;
            } catch (IOException dropped) {
                if (!running) {
                    break;
                }
            }
        }
    }

    private void handle(Socket socket) {
        try {
            handleOnce(socket);
        } catch (IOException ignored) {
            // Client went away; nothing to report.
        } finally {
            try {
                socket.close();
            } catch (IOException ignored) {
                // Closing only.
            }
        }
    }

    private void handleOnce(Socket socket) throws IOException {
        InputStream in = socket.getInputStream();
        OutputStream out = socket.getOutputStream();
        byte[] header = readHeader(in);
        if (header == null) {
            send(out, 400, "text/plain", "bad request");
            return;
        }
        String text = new String(header, StandardCharsets.ISO_8859_1);
        String[] lines = text.split("\r\n");
        String[] request = lines[0].split(" ");
        if (request.length != 3 || !request[2].startsWith("HTTP/")) {
            send(out, 400, "text/plain", "bad request line");
            return;
        }
        String method = request[0];
        String target = request[1];
        Map<String, String> headers = new HashMap<String, String>();
        for (int i = 1; i < lines.length; i++) {
            int colon = lines[i].indexOf(':');
            if (colon > 0) {
                headers.put(lines[i].substring(0, colon).trim()
                        .toLowerCase(Locale.US),
                        lines[i].substring(colon + 1).trim());
            }
        }
        String path;
        String query = "";
        int mark = target.indexOf('?');
        if (mark >= 0) {
            path = target.substring(0, mark);
            query = target.substring(mark + 1);
        } else {
            path = target;
        }
        if (!codeOk(query)) {
            if (queryHasCode(query)) {
                send(out, 403, "text/plain", "wrong access code");
            } else {
                send(out, 403, "text/plain", "missing access code");
            }
            return;
        }
        if (!method.equals("GET") && !method.equals("PUT")) {
            send(out, 405, "text/plain", "method not allowed");
            return;
        }
        if (path.equals("/") || path.equals("/files")) {
            if (!method.equals("GET")) {
                send(out, 405, "text/plain", "method not allowed");
                return;
            }
            sendList(out);
            return;
        }
        if (!path.startsWith("/files/")) {
            send(out, 404, "text/plain", "not found");
            return;
        }
        String raw = path.substring("/files/".length());
        String name;
        try {
            name = safeName(raw);
        } catch (HttpException refused) {
            send(out, refused.status, "text/plain", refused.getMessage());
            return;
        }
        Path target2;
        try {
            target2 = confinedTarget(name);
        } catch (HttpException refused) {
            send(out, refused.status, "text/plain", refused.getMessage());
            return;
        }
        if (method.equals("GET")) {
            sendFile(out, target2);
            return;
        }
        String lengthValue = headers.get("content-length");
        if (lengthValue == null) {
            send(out, 411, "text/plain", "length required");
            return;
        }
        long length;
        try {
            length = Long.parseLong(lengthValue);
        } catch (NumberFormatException bad) {
            send(out, 400, "text/plain", "bad content-length");
            return;
        }
        if (length < 0) {
            send(out, 400, "text/plain", "bad content-length");
            return;
        }
        storeFile(out, in, target2, length);
    }

    private byte[] readHeader(InputStream in) throws IOException {
        ByteArrayOutputStream header = new ByteArrayOutputStream();
        int[] tail = new int[]{-1, -1, -1, -1};
        while (header.size() < MAX_HEADER_BYTES) {
            int next = in.read();
            if (next < 0) {
                return null;
            }
            header.write(next);
            tail[0] = tail[1];
            tail[1] = tail[2];
            tail[2] = tail[3];
            tail[3] = next;
            if (tail[0] == '\r' && tail[1] == '\n'
                    && tail[2] == '\r' && tail[3] == '\n') {
                return header.toByteArray();
            }
        }
        return null;
    }

    private boolean queryHasCode(String query) {
        for (String pair : query.split("&")) {
            int equals = pair.indexOf('=');
            String key = equals >= 0
                    ? pair.substring(0, equals) : pair;
            if (key.equals("code")) {
                return true;
            }
        }
        return false;
    }

    private boolean codeOk(String query) {
        String have = null;
        for (String pair : query.split("&")) {
            int equals = pair.indexOf('=');
            if (equals < 0) {
                continue;
            }
            if (!pair.substring(0, equals).equals("code")) {
                continue;
            }
            try {
                have = percentDecodeOnce(pair.substring(equals + 1));
            } catch (HttpException bad) {
                return false;
            }
        }
        if (have == null || code == null) {
            return false;
        }
        return MessageDigest.isEqual(
                have.getBytes(StandardCharsets.UTF_8),
                code.getBytes(StandardCharsets.UTF_8));
    }

    /**
     * Validate one raw name segment. Refuses empty names, {@code ..},
     * absolute paths, slashes/backslashes at any percent-encoding
     * level, and control characters. Returns the decoded safe name.
     */
    static String safeName(String raw) throws HttpException {
        if (raw == null || raw.isEmpty()) {
            throw new HttpException(400, "empty file name");
        }
        if (raw.startsWith("/")) {
            throw new HttpException(400, "absolute path refused");
        }
        String current = raw;
        for (int round = 0; round < 3; round++) {
            if (current.contains("/") || current.contains("\\")) {
                throw new HttpException(400, "path separator refused");
            }
            if (!current.contains("%")) {
                break;
            }
            current = percentDecodeOnce(current);
        }
        if (current.contains("/") || current.contains("\\")) {
            throw new HttpException(400, "path separator refused");
        }
        if (current.isEmpty() || current.equals(".")) {
            throw new HttpException(400, "empty file name");
        }
        if (current.contains("..")) {
            throw new HttpException(400, "dot-dot refused");
        }
        if (current.length() > MAX_NAME_LENGTH) {
            throw new HttpException(400, "file name too long");
        }
        for (int i = 0; i < current.length(); i++) {
            char c = current.charAt(i);
            if (c < 0x20 || c == 0x7f) {
                throw new HttpException(400, "bad file name");
            }
        }
        return current;
    }

    /** Decode one percent-encoding level; rejects bad escapes and NUL. */
    static String percentDecodeOnce(String text) throws HttpException {
        ByteArrayOutputStream decoded = new ByteArrayOutputStream();
        byte[] bytes = text.getBytes(StandardCharsets.UTF_8);
        for (int i = 0; i < bytes.length; i++) {
            int c = bytes[i] & 0xff;
            if (c == '%') {
                if (i + 2 >= bytes.length) {
                    throw new HttpException(400, "bad percent escape");
                }
                int hi = Character.digit((char) bytes[i + 1], 16);
                int lo = Character.digit((char) bytes[i + 2], 16);
                if (hi < 0 || lo < 0) {
                    throw new HttpException(400, "bad percent escape");
                }
                int value = hi * 16 + lo;
                if (value == 0) {
                    throw new HttpException(400, "NUL refused");
                }
                decoded.write(value);
                i += 2;
            } else {
                decoded.write(c);
            }
        }
        return new String(decoded.toByteArray(), StandardCharsets.UTF_8);
    }

    /** Resolve a safe name inside the upload folder (links refused). */
    private Path confinedTarget(String name) throws HttpException {
        Path target = uploadDir.resolve(name).normalize();
        if (!target.startsWith(uploadDir)) {
            throw new HttpException(403, "path escapes upload folder");
        }
        try {
            if (Files.isSymbolicLink(target)) {
                throw new HttpException(403, "link refused");
            }
        } catch (SecurityException denied) {
            throw new HttpException(403, "link refused");
        }
        return target;
    }

    private void sendList(OutputStream out) throws IOException {
        StringBuilder body = new StringBuilder("{\"files\":[");
        boolean first = true;
        try (DirectoryStream<Path> stream =
                Files.newDirectoryStream(uploadDir)) {
            for (Path entry : stream) {
                if (!Files.isRegularFile(entry)) {
                    continue;
                }
                if (!first) {
                    body.append(',');
                }
                first = false;
                body.append("{\"name\":\"");
                body.append(jsonEscape(
                        entry.getFileName().toString()));
                body.append("\",\"bytes\":");
                body.append(Files.size(entry));
                body.append('}');
            }
        }
        body.append("]}");
        send(out, 200, "application/json", body.toString());
    }

    private void sendFile(OutputStream out, Path target)
            throws IOException {
        if (!Files.isRegularFile(target)) {
            send(out, 404, "text/plain", "not found");
            return;
        }
        byte[] data = Files.readAllBytes(target);
        sendBytes(out, 200, "application/octet-stream", data);
    }

    /**
     * Store an upload. Both quotas are checked before a single body
     * byte is read; the body lands in a temp file that is moved into
     * place only on success, so a refused upload leaves no partial
     * file behind.
     */
    private void storeFile(OutputStream out, InputStream in, Path target,
                           long length) throws IOException {
        synchronized (writeLock) {
            if (Files.isSymbolicLink(target)) {
                send(out, 403, "text/plain", "link refused");
                return;
            }
            if (length > maxUploadBytes) {
                send(out, 413, "text/plain", "upload too large");
                return;
            }
            long total = dirTotalBytes();
            if (Files.isRegularFile(target)) {
                total -= Files.size(target);
            }
            if (total + length > maxTotalBytes) {
                send(out, 413, "text/plain", "storage full");
                return;
            }
            Path tmp = Files.createTempFile(uploadDir, ".upload-", ".tmp");
            try {
                OutputStream tmpOut = Files.newOutputStream(tmp);
                try {
                    long remaining = length;
                    byte[] buf = new byte[65536];
                    while (remaining > 0) {
                        int want = (int) Math.min(buf.length, remaining);
                        int got = in.read(buf, 0, want);
                        if (got < 0) {
                            send(out, 400, "text/plain",
                                    "short body");
                            return;
                        }
                        tmpOut.write(buf, 0, got);
                        remaining -= got;
                    }
                } finally {
                    tmpOut.close();
                }
                try {
                    Files.move(tmp, target,
                            StandardCopyOption.ATOMIC_MOVE,
                            StandardCopyOption.REPLACE_EXISTING);
                } catch (IOException atomic) {
                    Files.move(tmp, target,
                            StandardCopyOption.REPLACE_EXISTING);
                }
            } finally {
                Files.deleteIfExists(tmp);
            }
        }
        send(out, 200, "application/json",
                "{\"name\":\"" + jsonEscape(
                        target.getFileName().toString()) + "\"}");
    }

    private static String jsonEscape(String text) {
        StringBuilder escaped = new StringBuilder();
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            switch (c) {
                case '"':
                    escaped.append("\\\"");
                    break;
                case '\\':
                    escaped.append("\\\\");
                    break;
                default:
                    if (c < 0x20) {
                        escaped.append(String.format("\\u%04x", (int) c));
                    } else {
                        escaped.append(c);
                    }
            }
        }
        return escaped.toString();
    }

    private static void send(OutputStream out, int status,
                             String contentType, String body)
            throws IOException {
        sendBytes(out, status, contentType,
                body.getBytes(StandardCharsets.UTF_8));
    }

    private static void sendBytes(OutputStream out, int status,
                                  String contentType, byte[] body)
            throws IOException {
        String head = "HTTP/1.1 " + status + " " + reason(status) + "\r\n"
                + "Content-Type: " + contentType + "\r\n"
                + "Content-Length: " + body.length + "\r\n"
                + "Connection: close\r\n"
                + "\r\n";
        out.write(head.getBytes(StandardCharsets.ISO_8859_1));
        out.write(body);
        out.flush();
    }

    private static String reason(int status) {
        switch (status) {
            case 200:
                return "OK";
            case 400:
                return "Bad Request";
            case 403:
                return "Forbidden";
            case 404:
                return "Not Found";
            case 405:
                return "Method Not Allowed";
            case 411:
                return "Length Required";
            case 413:
                return "Content Too Large";
            default:
                return "Error";
        }
    }

    /** HTTP failure with its status code. */
    static class HttpException extends Exception {
        final int status;

        HttpException(int status, String message) {
            super(message);
            this.status = status;
        }
    }
}
