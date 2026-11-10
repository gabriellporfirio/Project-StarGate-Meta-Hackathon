package com.stargate.hub;

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
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * The one server on the phone (plain Java, no Android classes, so the
 * host tests and the preview run this exact file).
 *
 * <p>It serves two pages and one small API:
 * <ul>
 * <li>{@code /phone}: the phone's own screen (settings and controls
 * only). Only the phone's screen holds the screen token.
 * <li>{@code /}: the management page, opened from a computer. It asks
 * for the access code shown on the phone, then gives files and media,
 * the assistant chat and the password vault.
 * </ul>
 * Rules, all enforced here:
 * <ul>
 * <li>Listens on 127.0.0.1 only. A computer reaches it through the
 * cable; nothing listens on the network.
 * <li>Requests must name 127.0.0.1 or localhost as their host, and
 * every change must carry the page's own header, so another web site
 * cannot drive this server through the browser.
 * <li>Wrong access codes are slowed down more each time.
 * <li>File names stay inside the files folder; uploads land in a temp
 * file first and never fill the phone completely.
 * <li>Only the services chosen for this build exist, and a stopped
 * service refuses its requests.
 * </ul>
 */
public final class HubServer {
    public static final String BIND_ADDRESS = "127.0.0.1";
    public static final int DEFAULT_PORT = 8080;
    public static final long DEFAULT_RESERVE_BYTES = 256L * 1024 * 1024;
    public static final String FILES = "files";
    public static final String ASSISTANT = "assistant";
    public static final String VAULT = "vault";

    private static final int MAX_HEADER_BYTES = 32 * 1024;
    private static final int MAX_JSON_BYTES = 256 * 1024;
    private static final int MAX_QUESTION_CHARS = 4000;
    private static final long SESSION_IDLE_MS = 12 * 60 * 60 * 1000L;
    private static final String CSP = "default-src 'none'; "
            + "script-src 'self'; style-src 'self'; img-src 'self'; "
            + "media-src 'self'; font-src 'self'; connect-src 'self'; "
            + "base-uri 'none'; form-action 'none'; "
            + "frame-ancestors 'none'";
    private static final String[][] TITLES = {
        {FILES, "Files and media"},
        {ASSISTANT, "AI assistant"},
        {VAULT, "Password vault"},
    };

    private final Path filesDir;
    private final File codeFile;
    private final File stateFile;
    private final int port;
    private final Assets assets;
    private final Device device;
    private final AskBackend ask;
    private final Vault vault;
    private final List<String> services;
    private final String buildId;
    private final String screenToken;
    private final SecureRandom random = new SecureRandom();
    private final Object writeLock = new Object();
    private final Map<String, Long> sessions = new HashMap<String, Long>();
    private final Map<String, Boolean> running =
            new LinkedHashMap<String, Boolean>();
    private final AtomicBoolean asking = new AtomicBoolean(false);
    private long reserveBytes = DEFAULT_RESERVE_BYTES;
    private String assistantMode;
    private volatile String code;
    private int codeFailures;
    private long codeBlockedUntilMs;
    private volatile boolean serving;
    private ServerSocket serverSocket;

    public HubServer(File dataDir, int port, Assets assets, Device device,
                     AskBackend ask, List<String> services,
                     String buildId, String screenToken)
            throws IOException {
        Files.createDirectories(dataDir.toPath());
        this.filesDir = new File(dataDir, "files").toPath()
                .toAbsolutePath().normalize();
        Files.createDirectories(filesDir);
        this.codeFile = new File(dataDir, "access-code");
        this.stateFile = new File(dataDir, "hub-state.json");
        this.port = port;
        this.assets = assets;
        this.device = device;
        this.ask = ask;
        this.vault = new Vault(new File(dataDir, "vault.json"));
        this.services = new ArrayList<String>();
        for (String[] known : TITLES) {
            if (services.contains(known[0])) {
                this.services.add(known[0]);
                running.put(known[0], Boolean.TRUE);
            }
        }
        this.buildId = buildId;
        this.screenToken = screenToken;
        this.assistantMode = ask.localName() != null ? "local" : "cloud";
        loadState();
        ensureCode();
    }

    /** For tests: how much free space uploads must leave on the phone. */
    public void setReserveBytes(long bytes) {
        reserveBytes = bytes;
    }

    public synchronized void start() throws IOException {
        if (serving) {
            return;
        }
        serverSocket = new ServerSocket(port, 50,
                InetAddress.getByName(BIND_ADDRESS));
        serving = true;
        Thread acceptor = new Thread(new Runnable() {
            @Override
            public void run() {
                acceptLoop();
            }
        }, "hub-accept");
        acceptor.setDaemon(true);
        acceptor.start();
    }

    public synchronized void stop() throws IOException {
        serving = false;
        if (serverSocket != null) {
            serverSocket.close();
            serverSocket = null;
        }
    }

    public int getPort() {
        ServerSocket socket = serverSocket;
        return socket == null ? port : socket.getLocalPort();
    }

    public String getCode() {
        return code;
    }

    public Vault getVault() {
        return vault;
    }

    // ---- state -----------------------------------------------------

    private void ensureCode() throws IOException {
        if (codeFile.isFile()) {
            String stored = new String(
                    Files.readAllBytes(codeFile.toPath()),
                    StandardCharsets.UTF_8).trim();
            if (stored.matches("[0-9]{6}")) {
                code = stored;
                return;
            }
        }
        newCode();
    }

    private synchronized void newCode() throws IOException {
        StringBuilder made = new StringBuilder();
        for (int i = 0; i < 6; i++) {
            made.append((char) ('0' + random.nextInt(10)));
        }
        writeAtomic(codeFile.toPath(),
                made.toString().getBytes(StandardCharsets.UTF_8));
        code = made.toString();
        sessions.clear();
        codeFailures = 0;
        codeBlockedUntilMs = 0;
    }

    private void loadState() {
        if (!stateFile.isFile()) {
            return;
        }
        try {
            Map<String, Object> stored = Json.object(new String(
                    Files.readAllBytes(stateFile.toPath()),
                    StandardCharsets.UTF_8));
            Object stopped = stored.get("stopped");
            if (stopped instanceof List) {
                for (Object id : (List<?>) stopped) {
                    if (running.containsKey(id)) {
                        running.put((String) id, Boolean.FALSE);
                    }
                }
            }
            String mode = Json.str(stored, "assistant_mode");
            if (modeAllowed(mode)) {
                assistantMode = mode;
            }
        } catch (IOException | Json.Bad | RuntimeException ignored) {
            // A broken state file only loses the last switches.
        }
    }

    private synchronized void saveState() throws IOException {
        List<Object> stopped = new ArrayList<Object>();
        for (Map.Entry<String, Boolean> entry : running.entrySet()) {
            if (!entry.getValue().booleanValue()) {
                stopped.add(entry.getKey());
            }
        }
        writeAtomic(stateFile.toPath(), Json.write(Json.map(
                "stopped", stopped, "assistant_mode", assistantMode))
                .getBytes(StandardCharsets.UTF_8));
    }

    private static void writeAtomic(Path target, byte[] data)
            throws IOException {
        Path tmp = Files.createTempFile(target.getParent(), ".hub-",
                ".tmp");
        try {
            Files.write(tmp, data);
            try {
                Files.move(tmp, target, StandardCopyOption.ATOMIC_MOVE,
                        StandardCopyOption.REPLACE_EXISTING);
            } catch (IOException plain) {
                Files.move(tmp, target,
                        StandardCopyOption.REPLACE_EXISTING);
            }
        } finally {
            Files.deleteIfExists(tmp);
        }
    }

    private boolean modeAllowed(String mode) {
        return "cloud".equals(mode)
                || ("local".equals(mode) && ask.localName() != null);
    }

    private synchronized boolean isRunning(String service) {
        Boolean on = running.get(service);
        return on != null && on.booleanValue();
    }

    private long filesBytes() throws IOException {
        long total = 0;
        try (DirectoryStream<Path> stream =
                Files.newDirectoryStream(filesDir)) {
            for (Path entry : stream) {
                if (Files.isRegularFile(entry) && !hidden(entry)) {
                    total += Files.size(entry);
                }
            }
        }
        return total;
    }

    private static boolean hidden(Path entry) {
        return entry.getFileName().toString().startsWith(".upload-");
    }

    private synchronized Map<String, Object> state(String role)
            throws IOException {
        List<Object> list = new ArrayList<Object>();
        for (String[] known : TITLES) {
            if (services.contains(known[0])) {
                list.add(Json.map("id", known[0], "title", known[1],
                        "running",
                        Boolean.valueOf(isRunning(known[0]))));
            }
        }
        File disk = filesDir.toFile();
        Map<String, Object> out = Json.map(
                "role", role,
                "build_id", buildId,
                "services", list,
                "network", Json.map(
                        "on", Boolean.valueOf(device.networkOn()),
                        "can_switch",
                        Boolean.valueOf(device.canSwitchNetwork())),
                "battery", Json.map(
                        "percent", Long.valueOf(device.batteryPercent()),
                        "plugged", Boolean.valueOf(device.pluggedIn())),
                "can_reboot", Boolean.valueOf(device.canReboot()),
                "storage", Json.map(
                        "free", Long.valueOf(disk.getUsableSpace()),
                        "total", Long.valueOf(disk.getTotalSpace()),
                        "files", Long.valueOf(filesBytes())));
        if (services.contains(ASSISTANT)) {
            out.put("assistant", Json.map(
                    "mode", assistantMode,
                    "local_name", ask.localName(),
                    "cloud_name", ask.cloudName(),
                    "key_set", Boolean.valueOf(ask.keySet())));
        }
        if (services.contains(VAULT)) {
            out.put("vault", Json.map(
                    "exists", Boolean.valueOf(vault.exists()),
                    "unlocked", Boolean.valueOf(vault.unlocked())));
        }
        if ("screen".equals(role)) {
            out.put("code", code);
        }
        return out;
    }

    // ---- HTTP plumbing ---------------------------------------------

    private void acceptLoop() {
        while (serving) {
            try {
                final Socket socket = serverSocket.accept();
                Thread worker = new Thread(new Runnable() {
                    @Override
                    public void run() {
                        try {
                            socket.setSoTimeout(60000);
                            handle(socket);
                        } catch (IOException ignored) {
                            // The other side went away.
                        } finally {
                            try {
                                socket.close();
                            } catch (IOException ignored) {
                                // Closing only.
                            }
                        }
                    }
                }, "hub-worker");
                worker.setDaemon(true);
                worker.start();
            } catch (SocketException done) {
                break;
            } catch (IOException dropped) {
                if (!serving) {
                    break;
                }
            }
        }
    }

    /** One parsed request. */
    private static final class Request {
        String method;
        String path;
        String query = "";
        Map<String, String> headers = new HashMap<String, String>();
        InputStream in;
        OutputStream out;
        String role;
        String newCookie;

        String header(String name) {
            return headers.get(name);
        }
    }

    /** A plain failure answer. */
    private static final class Fail extends Exception {
        final int status;

        Fail(int status, String message) {
            super(message);
            this.status = status;
        }
    }

    private void handle(Socket socket) throws IOException {
        Request req = new Request();
        req.in = socket.getInputStream();
        req.out = socket.getOutputStream();
        byte[] header = readHeader(req.in);
        if (header == null) {
            sendText(req, 400, "bad request");
            return;
        }
        String[] lines = new String(header, StandardCharsets.ISO_8859_1)
                .split("\r\n");
        String[] first = lines[0].split(" ");
        if (first.length != 3 || !first[2].startsWith("HTTP/")) {
            sendText(req, 400, "bad request");
            return;
        }
        req.method = first[0];
        int mark = first[1].indexOf('?');
        req.path = mark >= 0 ? first[1].substring(0, mark) : first[1];
        req.query = mark >= 0 ? first[1].substring(mark + 1) : "";
        for (int i = 1; i < lines.length; i++) {
            int colon = lines[i].indexOf(':');
            if (colon > 0) {
                req.headers.put(lines[i].substring(0, colon).trim()
                        .toLowerCase(Locale.US),
                        lines[i].substring(colon + 1).trim());
            }
        }
        try {
            if (!hostOk(req.header("host"))) {
                throw new Fail(403, "This page only answers on the "
                        + "phone's own address.");
            }
            route(req);
        } catch (Fail fail) {
            sendJson(req, fail.status,
                    Json.map("error", fail.getMessage()));
        } catch (Vault.Refused refused) {
            sendJson(req, refused.status,
                    Json.map("error", refused.getMessage()));
        } catch (Json.Bad bad) {
            sendJson(req, 400, Json.map("error", "bad request"));
        }
    }

    private static boolean hostOk(String host) {
        if (host == null) {
            return false;
        }
        String name = host;
        int colon = name.lastIndexOf(':');
        if (colon >= 0) {
            name = name.substring(0, colon);
        }
        return name.equals("127.0.0.1") || name.equals("localhost");
    }

    private byte[] readHeader(InputStream in) throws IOException {
        ByteArrayOutputStream header = new ByteArrayOutputStream();
        int state = 0;
        while (header.size() < MAX_HEADER_BYTES) {
            int next = in.read();
            if (next < 0) {
                return null;
            }
            header.write(next);
            if (next == '\r') {
                state = state == 2 ? 3 : 1;
            } else if (next == '\n') {
                if (state == 3) {
                    return header.toByteArray();
                }
                state = state == 1 ? 2 : 0;
            } else {
                state = 0;
            }
        }
        return null;
    }

    private static Map<String, String> cookies(Request req) {
        Map<String, String> found = new HashMap<String, String>();
        String raw = req.header("cookie");
        if (raw != null) {
            for (String part : raw.split(";")) {
                int equals = part.indexOf('=');
                if (equals > 0) {
                    found.put(part.substring(0, equals).trim(),
                            part.substring(equals + 1).trim());
                }
            }
        }
        return found;
    }

    private static boolean same(String a, String b) {
        return a != null && b != null && MessageDigest.isEqual(
                a.getBytes(StandardCharsets.UTF_8),
                b.getBytes(StandardCharsets.UTF_8));
    }

    private synchronized String roleOf(Request req) {
        Map<String, String> jar = cookies(req);
        if (screenToken != null && same(jar.get("screen"), screenToken)) {
            return "screen";
        }
        String sid = jar.get("sid");
        long now = System.currentTimeMillis();
        if (sid != null) {
            Long seen = sessions.get(sid);
            if (seen != null && now - seen.longValue() < SESSION_IDLE_MS) {
                sessions.put(sid, Long.valueOf(now));
                return "portal";
            }
            sessions.remove(sid);
        }
        return null;
    }

    /** Check an access code, slowing down repeated wrong tries. */
    private synchronized void checkCode(String given) throws Fail {
        long wait = codeBlockedUntilMs - System.currentTimeMillis();
        if (wait > 0) {
            throw new Fail(429, "Too many wrong tries. Wait "
                    + (wait / 1000 + 1) + " seconds.");
        }
        String digits = given == null ? "" : given.replaceAll("[^0-9]", "");
        if (same(digits, code)) {
            codeFailures = 0;
            return;
        }
        codeFailures++;
        if (codeFailures >= 5) {
            long seconds = Math.min(600L,
                    30L << Math.min(codeFailures - 5, 5));
            codeBlockedUntilMs = System.currentTimeMillis()
                    + seconds * 1000L;
        }
        throw new Fail(403, "That is not the code on the phone.");
    }

    private Map<String, Object> body(Request req)
            throws IOException, Fail, Json.Bad {
        long length = contentLength(req);
        if (length > MAX_JSON_BYTES) {
            throw new Fail(413, "Too much to send at once.");
        }
        byte[] data = new byte[(int) length];
        int got = 0;
        while (got < data.length) {
            int more = req.in.read(data, got, data.length - got);
            if (more < 0) {
                throw new Fail(400, "bad request");
            }
            got += more;
        }
        if (data.length == 0) {
            return new LinkedHashMap<String, Object>();
        }
        return Json.object(new String(data, StandardCharsets.UTF_8));
    }

    private static long contentLength(Request req) throws Fail {
        String value = req.header("content-length");
        if (value == null) {
            throw new Fail(411, "length required");
        }
        try {
            long length = Long.parseLong(value);
            if (length < 0) {
                throw new Fail(400, "bad request");
            }
            return length;
        } catch (NumberFormatException bad) {
            throw new Fail(400, "bad request");
        }
    }

    // ---- routing ---------------------------------------------------

    private void route(Request req)
            throws IOException, Fail, Vault.Refused, Json.Bad {
        String path = req.path;
        String method = req.method;
        if (!path.startsWith("/api/")) {
            if (!method.equals("GET")) {
                throw new Fail(405, "method not allowed");
            }
            if (path.equals("/")) {
                sendAsset(req, "portal.html");
            } else if (path.equals("/phone")) {
                sendAsset(req, "phone.html");
            } else if (path.startsWith("/ui/")
                    && path.substring(4).matches(
                            "[a-z0-9][a-z0-9-]*\\.(css|js|png|woff2)")) {
                sendAsset(req, path.substring(4));
            } else {
                throw new Fail(404, "not found");
            }
            return;
        }
        boolean change = !method.equals("GET");
        if (change && !"1".equals(req.header("x-hub"))) {
            throw new Fail(403, "This request did not come from the "
                    + "phone's page.");
        }
        req.role = roleOf(req);
        if (path.equals("/api/login") && method.equals("POST")) {
            checkCode(Json.str(body(req), "code"));
            byte[] raw = new byte[24];
            random.nextBytes(raw);
            StringBuilder sid = new StringBuilder();
            for (byte b : raw) {
                sid.append(String.format("%02x", b & 0xff));
            }
            synchronized (this) {
                sessions.put(sid.toString(),
                        Long.valueOf(System.currentTimeMillis()));
            }
            req.newCookie = "sid=" + sid
                    + "; Path=/; HttpOnly; SameSite=Strict";
            sendJson(req, 200, Json.map("ok", Boolean.TRUE));
            return;
        }
        if (req.role == null) {
            throw new Fail(401, "Enter the access code shown on the "
                    + "phone.");
        }
        boolean screen = req.role.equals("screen");
        if (path.equals("/api/state") && method.equals("GET")) {
            sendJson(req, 200, state(req.role));
        } else if (path.equals("/api/logout") && method.equals("POST")) {
            String sid = cookies(req).get("sid");
            synchronized (this) {
                sessions.remove(sid);
            }
            req.newCookie = "sid=; Path=/; HttpOnly; SameSite=Strict; "
                    + "Max-Age=0";
            sendJson(req, 200, Json.map("ok", Boolean.TRUE));
        } else if (path.equals("/api/service") && method.equals("POST")) {
            needScreen(screen);
            Map<String, Object> in = body(req);
            String id = Json.str(in, "id");
            String action = Json.str(in, "action");
            if (!services.contains(id)) {
                throw new Fail(404, "This phone does not have that "
                        + "service.");
            }
            synchronized (this) {
                if ("stop".equals(action)) {
                    running.put(id, Boolean.FALSE);
                    if (VAULT.equals(id)) {
                        vault.lock();
                    }
                } else if ("start".equals(action)
                        || "restart".equals(action)) {
                    if (VAULT.equals(id)) {
                        vault.lock();
                    }
                    running.put(id, Boolean.TRUE);
                } else {
                    throw new Fail(422, "unknown action");
                }
                saveState();
            }
            sendJson(req, 200, state(req.role));
        } else if (path.equals("/api/code/new") && method.equals("POST")) {
            needScreen(screen);
            newCode();
            sendJson(req, 200, state(req.role));
        } else if (path.equals("/api/device") && method.equals("POST")) {
            needScreen(screen);
            String action = Json.str(body(req), "action");
            boolean done;
            if ("network_off".equals(action)) {
                done = device.canSwitchNetwork()
                        && device.setNetwork(false);
            } else if ("network_on".equals(action)) {
                done = device.canSwitchNetwork() && device.setNetwork(true);
            } else if ("reboot".equals(action)) {
                done = device.canReboot() && device.reboot();
            } else {
                throw new Fail(422, "unknown action");
            }
            if (!done) {
                throw new Fail(409, "This phone does not let the "
                        + "system do that.");
            }
            sendJson(req, 200, state(req.role));
        } else if (path.startsWith("/api/files")) {
            needService(FILES);
            files(req, path.substring("/api/files".length()));
        } else if (path.startsWith("/api/assistant/")) {
            needService(ASSISTANT);
            assistant(req, path.substring("/api/assistant/".length()));
        } else if (path.startsWith("/api/vault")) {
            needService(VAULT);
            if (screen) {
                if (path.equals("/api/vault/lock")
                        && method.equals("POST")) {
                    vault.lock();
                    sendJson(req, 200, state(req.role));
                    return;
                }
                throw new Fail(403, "Passwords open on your computer, "
                        + "never on the phone's screen.");
            }
            vault(req, path.substring("/api/vault".length()));
        } else {
            throw new Fail(404, "not found");
        }
    }

    private static void needScreen(boolean screen) throws Fail {
        if (!screen) {
            throw new Fail(403, "Only the phone's own screen can do "
                    + "that.");
        }
    }

    private void needService(String id) throws Fail {
        if (!services.contains(id)) {
            throw new Fail(404, "This phone does not have that service.");
        }
        if (!isRunning(id)) {
            throw new Fail(503, "That service is stopped. Start it on "
                    + "the phone.");
        }
    }

    // ---- files -----------------------------------------------------

    private void files(Request req, String rest)
            throws IOException, Fail {
        String method = req.method;
        if (rest.isEmpty() || rest.equals("/")) {
            if (!method.equals("GET")) {
                throw new Fail(405, "method not allowed");
            }
            List<Object> list = new ArrayList<Object>();
            try (DirectoryStream<Path> stream =
                    Files.newDirectoryStream(filesDir)) {
                for (Path entry : stream) {
                    if (!Files.isRegularFile(entry) || hidden(entry)) {
                        continue;
                    }
                    String name = entry.getFileName().toString();
                    list.add(Json.map("name", name,
                            "bytes", Long.valueOf(Files.size(entry)),
                            "changed", Long.valueOf(Files
                                    .getLastModifiedTime(entry)
                                    .toMillis()),
                            "kind", Names.kind(name)));
                }
            }
            sendJson(req, 200, Json.map("files", list));
            return;
        }
        if (!rest.startsWith("/")) {
            throw new Fail(404, "not found");
        }
        String name;
        try {
            name = Names.safeName(rest.substring(1));
        } catch (Names.Refused refused) {
            throw new Fail(400, refused.getMessage());
        }
        Path target = filesDir.resolve(name).normalize();
        if (!target.startsWith(filesDir) || Files.isSymbolicLink(target)
                || name.startsWith(".")) {
            throw new Fail(403, "That name is not allowed.");
        }
        if (method.equals("GET")) {
            sendFile(req, target, name);
        } else if (method.equals("PUT")) {
            store(req, target, name);
        } else if (method.equals("DELETE")) {
            synchronized (writeLock) {
                if (!Files.isRegularFile(target)) {
                    throw new Fail(404, "That file is no longer there.");
                }
                Files.delete(target);
            }
            sendJson(req, 200, Json.map("ok", Boolean.TRUE));
        } else {
            throw new Fail(405, "method not allowed");
        }
    }

    private void store(Request req, Path target, String name)
            throws IOException, Fail {
        long length = contentLength(req);
        synchronized (writeLock) {
            long free = filesDir.toFile().getUsableSpace();
            long replaced = Files.isRegularFile(target)
                    ? Files.size(target) : 0;
            if (length > free + replaced - reserveBytes) {
                throw new Fail(413, "Not enough free space on the "
                        + "phone for that file.");
            }
            Path tmp = Files.createTempFile(filesDir, ".upload-", ".tmp");
            try {
                OutputStream tmpOut = Files.newOutputStream(tmp);
                try {
                    long remaining = length;
                    byte[] buf = new byte[65536];
                    while (remaining > 0) {
                        int got = req.in.read(buf, 0,
                                (int) Math.min(buf.length, remaining));
                        if (got < 0) {
                            throw new Fail(400, "The upload stopped "
                                    + "half way.");
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
                } catch (IOException plain) {
                    Files.move(tmp, target,
                            StandardCopyOption.REPLACE_EXISTING);
                }
            } finally {
                Files.deleteIfExists(tmp);
            }
        }
        sendJson(req, 200, Json.map("name", name));
    }

    private void sendFile(Request req, Path target, String name)
            throws IOException, Fail {
        if (!Files.isRegularFile(target)) {
            throw new Fail(404, "That file is no longer there.");
        }
        long size = Files.size(target);
        long from = 0;
        long to = size - 1;
        int status = 200;
        String range = req.header("range");
        if (range != null && range.startsWith("bytes=")
                && range.indexOf(',') < 0) {
            String spec = range.substring(6);
            int dash = spec.indexOf('-');
            try {
                if (dash == 0) {
                    from = Math.max(0, size - Long.parseLong(
                            spec.substring(1)));
                } else if (dash > 0) {
                    from = Long.parseLong(spec.substring(0, dash));
                    if (dash + 1 < spec.length()) {
                        to = Math.min(to, Long.parseLong(
                                spec.substring(dash + 1)));
                    }
                }
                status = 206;
            } catch (NumberFormatException bad) {
                status = 200;
                from = 0;
                to = size - 1;
            }
            if (status == 206 && (from > to || from >= size)) {
                StringBuilder head = head(416, "text/plain", 0, req);
                head.append("Content-Range: bytes */").append(size)
                        .append("\r\n\r\n");
                req.out.write(head.toString()
                        .getBytes(StandardCharsets.ISO_8859_1));
                req.out.flush();
                return;
            }
        }
        String type = Names.inlineType(name);
        boolean inline = type != null
                && !req.query.contains("download=1");
        long count = size == 0 ? 0 : to - from + 1;
        StringBuilder head = head(status,
                inline ? type : "application/octet-stream", count, req);
        head.append("Accept-Ranges: bytes\r\n");
        if (status == 206) {
            head.append("Content-Range: bytes ").append(from).append('-')
                    .append(to).append('/').append(size).append("\r\n");
        }
        if (!inline) {
            head.append("Content-Disposition: attachment; filename*="
                    + "UTF-8''").append(Names.encode(name)).append("\r\n");
        }
        head.append("\r\n");
        req.out.write(head.toString()
                .getBytes(StandardCharsets.ISO_8859_1));
        try (InputStream in = Files.newInputStream(target)) {
            long skip = from;
            while (skip > 0) {
                long skipped = in.skip(skip);
                if (skipped <= 0) {
                    break;
                }
                skip -= skipped;
            }
            byte[] buf = new byte[65536];
            long remaining = count;
            while (remaining > 0) {
                int got = in.read(buf, 0,
                        (int) Math.min(buf.length, remaining));
                if (got < 0) {
                    break;
                }
                req.out.write(buf, 0, got);
                remaining -= got;
            }
        }
        req.out.flush();
    }

    // ---- assistant -------------------------------------------------

    private void assistant(Request req, String what)
            throws IOException, Fail, Json.Bad {
        String method = req.method;
        if (what.equals("mode") && method.equals("POST")) {
            String mode = Json.str(body(req), "mode");
            if (!modeAllowed(mode)) {
                throw new Fail(422, "This phone cannot run a model "
                        + "itself. Use the cloud mode.");
            }
            synchronized (this) {
                assistantMode = mode;
                saveState();
            }
            sendJson(req, 200, state(req.role));
        } else if (what.equals("key") && method.equals("POST")) {
            String key = Json.str(body(req), "key");
            if (key == null || key.trim().isEmpty()
                    || key.length() > 512) {
                throw new Fail(422, "Paste the key from your provider.");
            }
            try {
                ask.saveKey(key.trim());
            } catch (Exception failed) {
                throw new Fail(500, "The phone could not save the key.");
            }
            sendJson(req, 200, state(req.role));
        } else if (what.equals("key") && method.equals("DELETE")) {
            ask.deleteKey();
            sendJson(req, 200, state(req.role));
        } else if (what.equals("ask") && method.equals("POST")) {
            String question = Json.str(body(req), "question");
            if (question == null || question.trim().isEmpty()) {
                throw new Fail(422, "Type a question first.");
            }
            if (question.length() > MAX_QUESTION_CHARS) {
                throw new Fail(413, "That question is too long.");
            }
            String mode;
            synchronized (this) {
                mode = assistantMode;
            }
            if (mode.equals("cloud") && !device.networkOn()) {
                throw new Fail(409, "The cloud mode needs the phone's "
                        + "network. Turn it on in Status.");
            }
            if (!asking.compareAndSet(false, true)) {
                throw new Fail(409, "Another question is running.");
            }
            AskBackend.Answer answer;
            long started = System.nanoTime();
            try {
                answer = ask.ask(mode, question.trim());
            } finally {
                asking.set(false);
            }
            long ms = (System.nanoTime() - started) / 1000000L;
            if (!answer.ok) {
                throw new Fail(502, answer.text);
            }
            sendJson(req, 200, Json.map("answer", answer.text,
                    "mode", mode, "ms", Long.valueOf(ms),
                    "name", mode.equals("local") ? ask.localName()
                            : ask.cloudName()));
        } else {
            throw new Fail(404, "not found");
        }
    }

    // ---- vault -----------------------------------------------------

    private void vault(Request req, String what)
            throws IOException, Fail, Vault.Refused, Json.Bad {
        String method = req.method;
        if (what.equals("/create") && method.equals("POST")) {
            vault.create(Json.str(body(req), "password"));
            sendJson(req, 200, Json.map("ok", Boolean.TRUE));
        } else if (what.equals("/unlock") && method.equals("POST")) {
            vault.unlock(Json.str(body(req), "password"));
            sendJson(req, 200, Json.map("ok", Boolean.TRUE));
        } else if (what.equals("/lock") && method.equals("POST")) {
            vault.lock();
            sendJson(req, 200, Json.map("ok", Boolean.TRUE));
        } else if (what.equals("/entries") && method.equals("GET")) {
            sendJson(req, 200, Json.map("entries", vault.list()));
        } else if (what.equals("/entries") && method.equals("POST")) {
            sendJson(req, 200, Json.map("id", vault.put(body(req))));
        } else if (what.startsWith("/entries/")
                && method.equals("DELETE")) {
            vault.remove(what.substring("/entries/".length()));
            sendJson(req, 200, Json.map("ok", Boolean.TRUE));
        } else if (what.equals("/password") && method.equals("POST")) {
            Map<String, Object> in = body(req);
            vault.changePassword(Json.str(in, "current"),
                    Json.str(in, "next"));
            sendJson(req, 200, Json.map("ok", Boolean.TRUE));
        } else {
            throw new Fail(404, "not found");
        }
    }

    // ---- answers ---------------------------------------------------

    private void sendAsset(Request req, String name)
            throws IOException, Fail {
        ByteArrayOutputStream data = new ByteArrayOutputStream();
        try (InputStream in = assets.open(name)) {
            byte[] buf = new byte[16384];
            int got;
            while ((got = in.read(buf)) >= 0) {
                data.write(buf, 0, got);
            }
        } catch (IOException missing) {
            throw new Fail(404, "not found");
        }
        String type = "application/octet-stream";
        if (name.endsWith(".html")) {
            type = "text/html; charset=utf-8";
        } else if (name.endsWith(".css")) {
            type = "text/css; charset=utf-8";
        } else if (name.endsWith(".js")) {
            type = "text/javascript; charset=utf-8";
        } else if (name.endsWith(".png")) {
            type = "image/png";
        } else if (name.endsWith(".woff2")) {
            type = "font/woff2";
        }
        sendBytes(req, 200, type, data.toByteArray());
    }

    private void sendJson(Request req, int status, Object value)
            throws IOException {
        sendBytes(req, status, "application/json",
                Json.write(value).getBytes(StandardCharsets.UTF_8));
    }

    private void sendText(Request req, int status, String text)
            throws IOException {
        sendBytes(req, status, "text/plain; charset=utf-8",
                text.getBytes(StandardCharsets.UTF_8));
    }

    private StringBuilder head(int status, String type, long length,
                               Request req) {
        StringBuilder head = new StringBuilder();
        head.append("HTTP/1.1 ").append(status).append(' ')
                .append(reason(status)).append("\r\n");
        head.append("Content-Type: ").append(type).append("\r\n");
        head.append("Content-Length: ").append(length).append("\r\n");
        head.append("Connection: close\r\n");
        head.append("Cache-Control: no-store\r\n");
        head.append("X-Content-Type-Options: nosniff\r\n");
        head.append("Referrer-Policy: no-referrer\r\n");
        head.append("Content-Security-Policy: ").append(CSP)
                .append("\r\n");
        if (req.newCookie != null) {
            head.append("Set-Cookie: ").append(req.newCookie)
                    .append("\r\n");
        }
        return head;
    }

    private void sendBytes(Request req, int status, String type,
                           byte[] data) throws IOException {
        StringBuilder head = head(status, type, data.length, req);
        head.append("\r\n");
        req.out.write(head.toString()
                .getBytes(StandardCharsets.ISO_8859_1));
        req.out.write(data);
        req.out.flush();
    }

    private static String reason(int status) {
        switch (status) {
            case 200:
                return "OK";
            case 206:
                return "Partial Content";
            case 400:
                return "Bad Request";
            case 401:
                return "Unauthorized";
            case 403:
                return "Forbidden";
            case 404:
                return "Not Found";
            case 405:
                return "Method Not Allowed";
            case 409:
                return "Conflict";
            case 411:
                return "Length Required";
            case 413:
                return "Content Too Large";
            case 416:
                return "Range Not Satisfiable";
            case 422:
                return "Unprocessable Content";
            case 423:
                return "Locked";
            case 429:
                return "Too Many Requests";
            case 503:
                return "Service Unavailable";
            default:
                return "Error";
        }
    }
}
