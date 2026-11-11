"""Host tests for the Files handler, against the SAME handler code.

These tests compile android/common/.../FilesServer.java (the exact
file baked into the APK) plus a small harness, run it on the host,
and exercise every section 3a rule over loopback HTTP.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import select
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from tests import workspace

REPO = Path(__file__).resolve().parent.parent
HANDLER = (REPO / "android" / "common" / "java" / "com" / "stargate"
           / "files" / "FilesServer.java")
HARNESS = (REPO / "android" / "test" / "java" / "com" / "stargate"
           / "files" / "FilesTestMain.java")
JAVAC = "/workspaces/m4-tools/jdk-17/bin/javac"
JAVA = "/workspaces/m4-tools/jdk-17/bin/java"


@workspace.needs_build_tools
class FilesServerHost(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for tool in (JAVAC, JAVA):
            assert os.path.isfile(tool), "missing read-only %s" % tool
        assert HANDLER.is_file(), "missing handler %s" % HANDLER
        assert HARNESS.is_file(), "missing harness %s" % HARNESS
        cls.work = tempfile.TemporaryDirectory()
        cls.classes = os.path.join(cls.work.name, "classes")
        os.makedirs(cls.classes)
        proc = subprocess.run(
            [JAVAC, "-d", cls.classes, str(HANDLER), str(HARNESS)],
            capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stderr
        cls._servers = []
        cls.storedir = tempfile.TemporaryDirectory()
        cls.server = cls.start_server(cls.storedir.name)

    @classmethod
    def tearDownClass(cls):
        for proc in cls._servers:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass
        cls.storedir.cleanup()
        cls.work.cleanup()

    @classmethod
    def start_server(cls, storedir, extra_args=()):
        uploads = os.path.join(storedir, "uploads")
        os.makedirs(uploads, exist_ok=True)
        proc = subprocess.Popen(
            [JAVA, "-cp", cls.classes,
             "com.stargate.files.FilesTestMain",
             "--storedir", storedir, "--port", "0"] + list(extra_args),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1)
        cls._servers.append(proc)
        ready, _, _ = select.select([proc.stdout], [], [], 30)
        assert ready, "test server did not start"
        line = proc.stdout.readline()
        match = re.match(r"READY addr=(\S+) port=(\d+) code=(\S+)", line)
        assert match, "bad READY line: %r" % line
        return {"proc": proc, "addr": match.group(1),
                "port": int(match.group(2)), "code": match.group(3),
                "uploads": uploads, "storedir": storedir}

    @classmethod
    def stop_server(cls, server):
        proc = server["proc"]
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass
        if proc in cls._servers:
            cls._servers.remove(proc)

    def http(self, method, path, body=None, server=None):
        server = server or self.server
        conn = http.client.HTTPConnection(
            "127.0.0.1", server["port"], timeout=15)
        try:
            conn.request(method, path, body=body or b"")
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def raw_request(self, payload: bytes, server=None):
        server = server or self.server
        sock = socket.create_connection(
            ("127.0.0.1", server["port"]), timeout=15)
        try:
            sock.sendall(payload)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
            head, _, rest = data.partition(b"\r\n\r\n")
            status = int(head.split(b" ", 2)[1])
            match = re.search(rb"Content-Length:\s*(\d+)", head,
                              re.IGNORECASE)
            want = int(match.group(1)) if match else 0
            while len(rest) < want:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                rest += chunk
            return status, rest
        finally:
            sock.close()

    def test_no_code_is_refused(self):
        status, _body = self.http("GET", "/files")
        self.assertEqual(status, 403)
        status, _body = self.http(
            "PUT", "/files/nocode.txt", body=b"x")
        self.assertEqual(status, 403)
        self.assertFalse(os.path.exists(os.path.join(
            self.server["uploads"], "nocode.txt")))

    def test_wrong_code_is_refused(self):
        status, _body = self.http("GET", "/files?code=wrong-code")
        self.assertEqual(status, 403)
        status, _body = self.http(
            "PUT", "/files/wrong.txt?code=wrong-code", body=b"x")
        self.assertEqual(status, 403)
        self.assertFalse(os.path.exists(os.path.join(
            self.server["uploads"], "wrong.txt")))

    def test_upload_download_roundtrip(self):
        code = self.server["code"]
        data = b"hello stargate\r\n" * 100
        status, _body = self.http(
            "PUT", "/files/hello.txt?code=%s" % code, body=data)
        self.assertEqual(status, 200)
        status, back = self.http(
            "GET", "/files/hello.txt?code=%s" % code)
        self.assertEqual(status, 200)
        self.assertEqual(back, data)
        status, listed = self.http("GET", "/files?code=%s" % code)
        self.assertEqual(status, 200)
        names = [item["name"] for item in json.loads(listed)["files"]]
        self.assertIn("hello.txt", names)

    def test_dotdot_refused(self):
        code = self.server["code"]
        for target in ("/files/../escape.txt?code=%s" % code,
                       "/files/..%%2fescape.txt?code=%s" % code,
                       "/files/%%2e%%2e%%2fescape.txt?code=%s" % code,
                       "/files/a..b.txt?code=%s" % code):
            with self.subTest(target=target):
                status, _body = self.http("PUT", target, body=b"x")
                self.assertEqual(status, 400)
        self.assertFalse(os.path.exists(os.path.join(
            self.server["storedir"], "escape.txt")))

    def test_absolute_path_refused(self):
        code = self.server["code"]
        status, _body = self.http(
            "PUT", "/files//etc-hosts?code=%s" % code, body=b"x")
        self.assertEqual(status, 400)

    def test_encoded_slashes_refused(self):
        code = self.server["code"]
        for target in ("/files/a%%2fb.txt?code=%s" % code,
                       "/files/a%%2Fb.txt?code=%s" % code,
                       "/files/a%%5cb.txt?code=%s" % code,
                       "/files/a%%252fb.txt?code=%s" % code):
            with self.subTest(target=target):
                status, _body = self.http("PUT", target, body=b"x")
                self.assertEqual(status, 400)

    def test_link_outside_refused(self):
        code = self.server["code"]
        with tempfile.TemporaryDirectory() as tmp:
            outside = os.path.join(tmp, "secret.txt")
            Path(outside).write_bytes(b"top-secret-bytes")
            link = os.path.join(self.server["uploads"], "link.pte")
            try:
                os.symlink(outside, link)
            except OSError as exc:
                self.fail("cannot make test symlink: %s" % exc)
            try:
                status, body = self.http(
                    "GET", "/files/link.pte?code=%s" % code)
                self.assertEqual(status, 403)
                self.assertNotIn(b"top-secret-bytes", body)
                status, _body = self.http(
                    "PUT", "/files/link.pte?code=%s" % code,
                    body=b"overwrite")
                self.assertEqual(status, 403)
                self.assertEqual(Path(outside).read_bytes(),
                                 b"top-secret-bytes")
            finally:
                os.unlink(link)

    def test_upload_over_50mb_refused_before_writing(self):
        code = self.server["code"]
        before = set(os.listdir(self.server["uploads"]))
        payload = ("PUT /files/big.bin?code=%s HTTP/1.1\r\n"
                   "Host: x\r\n"
                   "Content-Length: 62914560\r\n"
                   "Connection: close\r\n"
                   "\r\n" % code).encode()
        status, _body = self.raw_request(payload)
        self.assertEqual(status, 413)
        self.assertEqual(set(os.listdir(self.server["uploads"])), before)
        self.assertFalse(os.path.exists(os.path.join(
            self.server["uploads"], "big.bin")))

    def test_single_and_total_quotas_refused_with_no_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = self.start_server(
                tmp, ["--max-upload", "1024", "--max-total", "2048"])
            try:
                code = server["code"]
                status, _body = self.http(
                    "PUT", "/files/a.bin?code=%s" % code,
                    body=b"a" * 1000, server=server)
                self.assertEqual(status, 200)
                status, _body = self.http(
                    "PUT", "/files/b.bin?code=%s" % code,
                    body=b"b" * 1000, server=server)
                self.assertEqual(status, 200)
                status, _body = self.http(
                    "PUT", "/files/c.bin?code=%s" % code,
                    body=b"c" * 100, server=server)
                self.assertEqual(status, 413)
                self.assertFalse(os.path.exists(os.path.join(
                    server["uploads"], "c.bin")))
                status, _body = self.http(
                    "PUT", "/files/d.bin?code=%s" % code,
                    body=b"d" * 1025, server=server)
                self.assertEqual(status, 413)
                self.assertFalse(os.path.exists(os.path.join(
                    server["uploads"], "d.bin")))
                self.assertEqual(
                    Path(os.path.join(
                        server["uploads"], "a.bin")).read_bytes(),
                    b"a" * 1000)
            finally:
                self.stop_server(server)

    def test_default_limits_are_50_and_200mb(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [JAVA, "-cp", self.classes,
                 "com.stargate.files.FilesTestMain",
                 "--storedir", tmp, "--dump-limits"],
                capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("max_upload=52428800", proc.stdout)
        self.assertIn("max_total=209715200", proc.stdout)

    def test_missing_content_length_refused(self):
        code = self.server["code"]
        payload = ("PUT /files/nolen.txt?code=%s HTTP/1.1\r\n"
                   "Host: x\r\n"
                   "Connection: close\r\n"
                   "\r\n" % code).encode()
        status, _body = self.raw_request(payload)
        self.assertEqual(status, 411)
        self.assertFalse(os.path.exists(os.path.join(
            self.server["uploads"], "nolen.txt")))

    def test_listening_socket_is_loopback(self):
        self.assertEqual(self.server["addr"], "127.0.0.1")
        port_hex = "%04X" % self.server["port"]
        listen = []
        for table in ("/proc/net/tcp", "/proc/net/tcp6"):
            with open(table, encoding="utf-8") as handle:
                lines = handle.read().splitlines()
            listen.extend(line.split() for line in lines[1:]
                          if len(line.split()) >= 4
                          and line.split()[3] == "0A")
        loopback = ("0100007F:%s" % port_hex,
                    "0000000000000000FFFF00000100007F:%s" % port_hex,
                    "00000000000000000000000001000000:%s" % port_hex)
        bound = [row for row in listen if row[1] in loopback]
        self.assertTrue(
            bound,
            "no 127.0.0.1 LISTEN entry for port %d in /proc/net/tcp*"
            % self.server["port"])
        wildcard = [row for row in listen
                    if row[1] in ("00000000:%s" % port_hex,
                                  "00000000000000000000000000000000:%s"
                                  % port_hex)]
        self.assertEqual(wildcard, [])

    def test_code_made_once_and_shown(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = self.start_server(tmp)
            try:
                code = first["code"]
                self.assertRegex(code, r"[A-Za-z2-9]{12}")
                stored = Path(os.path.join(tmp, "access-code"))
                self.assertEqual(stored.read_text(
                    encoding="utf-8").strip(), code)
            finally:
                self.stop_server(first)
            time.sleep(0.2)
            second = self.start_server(tmp)
            try:
                self.assertEqual(second["code"], code)
            finally:
                self.stop_server(second)
        with tempfile.TemporaryDirectory() as tmp:
            other = self.start_server(tmp)
            try:
                self.assertNotEqual(other["code"], code)
            finally:
                self.stop_server(other)

    def test_no_outgoing_connections(self):
        text = HANDLER.read_text(encoding="utf-8")
        self.assertIn("new ServerSocket(", text)
        for token in ("new Socket(", "HttpURLConnection", "HttpClient",
                      "openConnection(", "DatagramSocket",
                      "SocketChannel.open(", "URL("):
            self.assertNotIn(token, text)


if __name__ == "__main__":
    unittest.main()
