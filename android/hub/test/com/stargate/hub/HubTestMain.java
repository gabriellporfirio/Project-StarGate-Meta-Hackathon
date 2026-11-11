package com.stargate.hub;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Arrays;
import java.util.Map;

/**
 * Host test for the phone's server: runs the real {@link HubServer}
 * against a temp folder and talks to it over real sockets. Prints one
 * line per check and exits 1 on the first failure.
 */
public final class HubTestMain {
    private static int port;
    private static int checks;

    private HubTestMain() {
    }

    /** status + body of one raw request. */
    static final class Reply {
        int status;
        String head;
        byte[] body;

        String text() {
            return new String(body, StandardCharsets.UTF_8);
        }
    }

    static Reply send(String method, String path, String cookie,
                      byte[] body, String... extra) throws Exception {
        try (Socket socket = new Socket("127.0.0.1", port)) {
            StringBuilder head = new StringBuilder();
            head.append(method).append(' ').append(path)
                    .append(" HTTP/1.1\r\n");
            boolean host = false;
            for (String line : extra) {
                head.append(line).append("\r\n");
                host |= line.toLowerCase().startsWith("host:");
            }
            if (!host) {
                head.append("Host: 127.0.0.1:").append(port)
                        .append("\r\n");
            }
            if (cookie != null) {
                head.append("Cookie: ").append(cookie).append("\r\n");
            }
            if (body != null) {
                head.append("Content-Length: ").append(body.length)
                        .append("\r\n");
            }
            head.append("\r\n");
            OutputStream out = socket.getOutputStream();
            out.write(head.toString().getBytes(StandardCharsets.ISO_8859_1));
            if (body != null) {
                out.write(body);
            }
            out.flush();
            InputStream in = socket.getInputStream();
            ByteArrayOutputStream all = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int got;
            while ((got = in.read(buf)) >= 0) {
                all.write(buf, 0, got);
            }
            byte[] raw = all.toByteArray();
            String text = new String(raw, StandardCharsets.ISO_8859_1);
            int split = text.indexOf("\r\n\r\n");
            Reply reply = new Reply();
            reply.head = text.substring(0, split);
            reply.status = Integer.parseInt(text.substring(9, 12));
            reply.body = Arrays.copyOfRange(raw, split + 4, raw.length);
            return reply;
        }
    }

    static Reply post(String path, String cookie, String json)
            throws Exception {
        return send("POST", path, cookie,
                json.getBytes(StandardCharsets.UTF_8), "X-Hub: 1");
    }

    static void check(String name, boolean ok) {
        checks++;
        System.out.println((ok ? "ok   " : "FAIL ") + name);
        if (!ok) {
            System.exit(1);
        }
    }

    public static void main(String[] args) throws Exception {
        File dir = Files.createTempDirectory("hub-test").toFile();
        File pages = new File(args[0]);
        Device.Fake device = new Device.Fake();
        AskBackend.Fake ask = new AskBackend.Fake();
        HubServer server = new HubServer(dir, 0,
                PreviewMain.folders(new String[]{pages.getPath()}),
                device, ask, Arrays.asList("files", "assistant", "vault"),
                "TEST-1", "screen-token");
        server.setReserveBytes(0);
        server.start();
        port = server.getPort();
        String screen = "screen=screen-token";
        String code = server.getCode();

        check("code is six digits", code.matches("[0-9]{6}"));
        check("pages are served", send("GET", "/", null, null).status == 200
                && send("GET", "/phone", null, null).status == 200
                && send("GET", "/ui/portal.js", null, null).status == 200);
        check("pages carry the content policy", send("GET", "/", null, null)
                .head.contains("Content-Security-Policy: default-src 'none'"));
        check("odd asset names are refused",
                send("GET", "/ui/..%2fx.js", null, null).status == 404);
        check("another host name is refused", send("GET", "/", null, null,
                "Host: evil.example").status == 403);
        check("state needs sign-in",
                send("GET", "/api/state", null, null).status == 401);
        check("files need sign-in",
                send("GET", "/api/files", null, null).status == 401);
        check("a change without the page header is refused",
                send("POST", "/api/login", null, ("{\"code\":\"" + code
                        + "\"}").getBytes(StandardCharsets.UTF_8))
                        .status == 403);
        check("wrong code is refused", post("/api/login", null,
                "{\"code\":\"000000x\"}").status == 403);
        Reply login = post("/api/login", null, "{\"code\":\""
                + code.substring(0, 3) + " " + code.substring(3) + "\"}");
        check("right code signs in", login.status == 200
                && login.head.contains("Set-Cookie: sid=")
                && login.head.contains("HttpOnly"));
        int at = login.head.indexOf("Set-Cookie: sid=") + 12;
        String sid = login.head.substring(at, login.head.indexOf(';', at));

        Reply state = send("GET", "/api/state", sid, null);
        check("the computer never sees the access code",
                state.status == 200 && !state.text().contains(code)
                        && state.text().contains("\"role\":\"portal\""));
        check("the phone screen sees the access code",
                send("GET", "/api/state", screen, null).text()
                        .contains("\"code\":\"" + code + "\""));

        byte[] data = "0123456789".getBytes(StandardCharsets.UTF_8);
        check("upload", send("PUT", "/api/files/song.mp3", sid, data,
                "X-Hub: 1").status == 200);
        check("upload with spaces", send("PUT", "/api/files/my%20notes.txt",
                sid, data, "X-Hub: 1").status == 200);
        Reply list = send("GET", "/api/files", sid, null);
        check("list shows both with kinds", list.text().contains(
                "\"name\":\"song.mp3\"") && list.text().contains(
                "\"kind\":\"music\"") && list.text().contains(
                "\"name\":\"my notes.txt\""));
        Reply part = send("GET", "/api/files/song.mp3", sid, null,
                "Range: bytes=2-5");
        check("range read for media", part.status == 206
                && part.text().equals("2345")
                && part.head.contains("Content-Range: bytes 2-5/10")
                && part.head.contains("Content-Type: audio/mpeg"));
        Reply txt = send("GET", "/api/files/my%20notes.txt", sid, null);
        check("other files only download", txt.head.contains(
                "Content-Disposition: attachment")
                && txt.head.contains("application/octet-stream"));
        check("html is never shown inline", send("PUT",
                "/api/files/page.html", sid, data, "X-Hub: 1").status == 200
                && send("GET", "/api/files/page.html", sid, null).head
                        .contains("Content-Disposition: attachment"));
        check("dot-dot refused", send("PUT", "/api/files/..%2f..%2fx", sid,
                data, "X-Hub: 1").status == 400);
        check("encoded slash refused", send("PUT", "/api/files/a%252fb",
                sid, data, "X-Hub: 1").status == 400);
        check("hidden names refused", send("PUT", "/api/files/.upload-x.tmp",
                sid, data, "X-Hub: 1").status == 403);
        check("nothing escaped the folder",
                !new File(dir.getParentFile(), "x").exists());
        check("delete", send("DELETE", "/api/files/song.mp3", sid, null,
                "X-Hub: 1").status == 200 && send("GET",
                "/api/files/song.mp3", sid, null).status == 404);

        check("computer cannot stop services", post("/api/service", sid,
                "{\"id\":\"files\",\"action\":\"stop\"}").status == 403);
        check("phone stops files", post("/api/service", screen,
                "{\"id\":\"files\",\"action\":\"stop\"}").status == 200);
        check("stopped service refuses",
                send("GET", "/api/files", sid, null).status == 503);
        check("phone starts files", post("/api/service", screen,
                "{\"id\":\"files\",\"action\":\"start\"}").status == 200
                && send("GET", "/api/files", sid, null).status == 200);

        check("vault starts empty", send("GET", "/api/state", sid, null)
                .text().contains("\"vault\":{\"exists\":false"));
        check("short master password refused", post("/api/vault/create",
                sid, "{\"password\":\"short\"}").status == 422);
        check("vault created", post("/api/vault/create", sid,
                "{\"password\":\"correct horse\"}").status == 200);
        Reply made = post("/api/vault/entries", sid, "{\"title\":\"Bank\","
                + "\"username\":\"sam\",\"password\":\"s3cret-value\"}");
        check("entry saved", made.status == 200);
        String vaultFile = new String(Files.readAllBytes(
                new File(dir, "vault.json").toPath()),
                StandardCharsets.UTF_8);
        check("nothing readable in the vault file",
                !vaultFile.contains("s3cret-value")
                        && !vaultFile.contains("Bank")
                        && !vaultFile.contains("correct horse")
                        && vaultFile.contains("aes-256-gcm"));
        check("phone screen cannot read passwords", send("GET",
                "/api/vault/entries", screen, null).status == 403);
        check("phone screen can lock", post("/api/vault/lock", screen,
                "{}").status == 200);
        check("locked vault refuses", send("GET", "/api/vault/entries",
                sid, null).status == 423);
        check("wrong master password", post("/api/vault/unlock", sid,
                "{\"password\":\"wrong horse\"}").status == 403);
        check("right master password", post("/api/vault/unlock", sid,
                "{\"password\":\"correct horse\"}").status == 200);
        Reply entries = send("GET", "/api/vault/entries", sid, null);
        check("entry comes back", entries.text().contains("s3cret-value"));
        Map<String, Object> first = Json.object(made.text());
        check("change master password", post("/api/vault/password", sid,
                "{\"current\":\"correct horse\",\"next\":\"new horse 22\"}")
                .status == 200);
        post("/api/vault/lock", sid, "{}");
        check("old master password no longer works", post(
                "/api/vault/unlock", sid,
                "{\"password\":\"correct horse\"}").status == 403);
        check("new master password works", post("/api/vault/unlock", sid,
                "{\"password\":\"new horse 22\"}").status == 200);
        check("entry deleted", send("DELETE", "/api/vault/entries/"
                + first.get("id"), sid, null, "X-Hub: 1").status == 200);
        post("/api/vault/lock", sid, "{}");
        for (int i = 0; i < 3; i++) {
            post("/api/vault/unlock", sid, "{\"password\":\"nope nope\"}");
        }
        check("repeated wrong tries are slowed", post("/api/vault/unlock",
                sid, "{\"password\":\"new horse 22\"}").status == 429);

        check("assistant answers on the phone", post("/api/assistant/ask",
                sid, "{\"question\":\"hi\"}").text().contains("\"mode\":"
                + "\"local\""));
        check("cloud mode", post("/api/assistant/mode", sid,
                "{\"mode\":\"cloud\"}").status == 200);
        check("cloud without key fails plainly", post("/api/assistant/ask",
                sid, "{\"question\":\"hi\"}").status == 502);
        Reply key = post("/api/assistant/key", sid,
                "{\"key\":\"example-key-1234567890\"}");
        check("key saved and never echoed", key.status == 200
                && key.text().contains("\"key_set\":true")
                && !key.text().contains("example-key"));
        check("cloud answers", post("/api/assistant/ask", sid,
                "{\"question\":\"hi\"}").status == 200);
        check("network off", post("/api/device", screen,
                "{\"action\":\"network_off\"}").status == 200
                && !device.network);
        check("cloud needs the network", post("/api/assistant/ask", sid,
                "{\"question\":\"hi\"}").status == 409);
        check("computer cannot reboot the phone", post("/api/device", sid,
                "{\"action\":\"reboot\"}").status == 403);

        check("new code signs the computer out", post("/api/code/new",
                screen, "{}").status == 200 && !server.getCode()
                .equals(code) && send("GET", "/api/state", sid, null)
                .status == 401);
        for (int i = 0; i < 5; i++) {
            post("/api/login", null, "{\"code\":\"000000x\"}");
        }
        check("repeated wrong codes are slowed", post("/api/login", null,
                "{\"code\":\"" + server.getCode() + "\"}").status == 429);

        server.stop();
        HubServer again = new HubServer(dir, 0,
                PreviewMain.folders(new String[]{pages.getPath()}),
                device, ask, Arrays.asList("files"), "TEST-2", "t2");
        again.start();
        port = again.getPort();
        check("code survives a restart",
                again.getCode().equals(server.getCode()));
        check("unchosen services do not exist", post("/api/vault/lock",
                "screen=t2", "{}").status == 404 && !send("GET",
                "/api/state", "screen=t2", null).text().contains("vault"));
        again.stop();
        System.out.println("ALL OK " + checks);
    }
}
