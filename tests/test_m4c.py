"""Offline M4c BYOK tests: providers, keys, variants, limits, log."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path

from stargate import m4c
from tests.test_assistant import JAVAC, JAVA
from tests import workspace

REPO = Path(__file__).resolve().parent.parent
M4B_TOOLS = Path(os.environ.get(
    "M4B_TOOLS", REPO / "../m4b-tools")).resolve()


def _manifest():
    with open(REPO / "assistant-manifest.json",
              encoding="utf-8") as handle:
        return json.load(handle)


def _apk_for(key):
    return M4B_TOOLS / _manifest()[key]["path"]


def _local_apk():
    return _apk_for("assistant_apk")


def _cloud_apk():
    return _apk_for("assistant_cloud_apk")


def _need_apk(testcase, apk):
    """Missing-APK policy: skip normally, fail under release check.

    APK-reading tests skip when their APK is not built yet, so a
    plain checkout stays green; with M4C_RELEASE_CHECK=1 a
    missing APK fails instead, so the release run proves the
    APK was really checked.
    """
    if not apk.exists():
        if os.environ.get("M4C_RELEASE_CHECK") == "1":
            testcase.fail("release check: missing built APK: %s"
                          % apk)
        testcase.skipTest("APK not built yet: %s" % apk)
    return apk


APP_JAVA = (REPO / "assistant-app" / "java" / "com" / "stargate"
            / "assistant")
MAIN_ACTIVITY = APP_JAVA / "MainActivity.java"

M4C_PURE = ["ChatFormat.java", "DeviceFacts.java", "AskFlow.java",
            "CloudSpec.java", "Json.java", "CloudRequest.java",
            "NetworkLayer.java", "HttpNetworkLayer.java",
            "ErrorPolicy.java", "CloudAsk.java",
            "KeyStorage.java", "ModeDispatch.java", "ModeLabel.java",
            "StatsLine.java"]

DUMMY_META = "DUMMY-META-KEY"
DUMMY_BASETEN = "DUMMY-BASETEN-KEY"

FAKE_CLOUD = """\
package com.stargate.assistant;

import java.net.HttpURLConnection;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

public class FakeCloud {
    static final class FakeNet implements NetworkLayer {
        int count = 0;
        final List<CloudRequest.WireRequest> seen =
                new ArrayList<CloudRequest.WireRequest>();
        final List<Integer> timeouts = new ArrayList<Integer>();
        final List<Response> script =
                new ArrayList<Response>();
        final List<Boolean> throwsIo =
                new ArrayList<Boolean>();
        ManualClock ticker = null;
        final List<Long> advances = new ArrayList<Long>();

        public Response send(CloudRequest.WireRequest req,
                int timeoutMs) throws java.io.IOException {
            seen.add(req);
            timeouts.add(timeoutMs);
            int i = count;
            count++;
            if (ticker != null) {
                long step = 50000;
                if (i < advances.size()) {
                    step = advances.get(i);
                }
                ticker.now += step;
            }
            if (i < throwsIo.size() && throwsIo.get(i)) {
                throw new java.io.IOException("timeout");
            }
            if (script.isEmpty()) {
                return new Response(200, "{}", -1, false);
            }
            if (i < script.size()) {
                return script.get(i);
            }
            return script.get(script.size() - 1);
        }
    }

    static final class FakeRunner implements AskFlow.Runner {
        int pos = 0;
        final StringBuilder log = new StringBuilder();

        public void resetContext() {
            log.append("reset\\n");
            pos = 0;
        }

        public void generate(String prompt,
                AskFlow.GenerateCallback cb) {
            log.append("generate start=" + pos + "\\n");
            pos += 67;
        }
    }

    static final class Nop implements AskFlow.GenerateCallback {
        public void onResult(String token) {
        }

        public void onStats(String stats) {
        }

        public void onError(int code, String message) {
        }

        public void onAppAnswer(String answer) {
        }
    }

    static final class CloudCb implements CloudAsk.Callback {
        String event = "";

        public void onAnswer(String text, boolean incomplete,
                boolean truncated, long inTok, long outTok) {
            event = "answer|" + text + "|" + incomplete + "|"
                    + truncated + "|" + inTok + "|" + outTok;
        }

        public void onError(String message) {
            event = "error|" + message;
        }

        public void onRefused(String message) {
            event = "refused|" + message;
        }
    }

    static final class ManualClock implements CloudAsk.Clock {
        long now = 1000;

        public long nowMs() {
            return now;
        }
    }

    static final class RecSleeper implements CloudAsk.Sleeper {
        final List<Long> sleeps = new ArrayList<Long>();
        ManualClock clock = null;

        public void sleepMs(long ms) {
            sleeps.add(ms);
            if (clock != null) {
                clock.now += ms;
            }
        }
    }

    static final class FakeConn extends HttpURLConnection {
        boolean follow = true;
        int connectTo = -1;
        int readTo = -1;

        FakeConn() {
            super(null);
        }

        public void connect() {
        }

        public void disconnect() {
        }

        public boolean usingProxy() {
            return false;
        }

        public void setInstanceFollowRedirects(boolean f) {
            follow = f;
        }

        public void setConnectTimeout(int t) {
            connectTo = t;
        }

        public void setReadTimeout(int t) {
            readTo = t;
        }
    }

    static final class Trickle extends java.io.InputStream {
        final ManualClock clock;
        final long step;
        int left;
        boolean closed = false;

        Trickle(ManualClock clock, long step, int bytes) {
            this.clock = clock;
            this.step = step;
            this.left = bytes;
        }

        public int read(byte[] buf, int off, int len) {
            if (left <= 0) {
                return -1;
            }
            clock.now += step;
            buf[off] = (byte) 'x';
            left--;
            return 1;
        }

        public int read() {
            byte[] one = new byte[1];
            int got = read(one, 0, 1);
            if (got < 0) {
                return -1;
            }
            return one[0] & 0xFF;
        }

        public void close() {
            closed = true;
        }
    }

    static String reply(String text, String finish, boolean usage) {
        String use = usage ? ",\\"usage\\":{\\"prompt_tokens\\":10,"
                + "\\"completion_tokens\\":5}" : "";
        return "{\\"choices\\":[{\\"message\\":{\\"content\\":\\""
                + text + "\\"},\\"finish_reason\\":"
                + (finish == null ? "null"
                        : "\\"" + finish + "\\"") + "}]" + use
                + "}";
    }

    public static void main(String[] args) throws Exception {
        String scene = args[0];
        if (scene.equals("route")) {
            String where =
                    ModeDispatch.route(args[1], args[2]);
            System.out.println("route=" + where);
            if (where.equals(ModeDispatch.CLOUD)) {
                System.out.println("provider="
                        + ModeDispatch.providerFor(args[1]));
            }
            return;
        }
        if (scene.equals("providererr")) {
            try {
                ModeDispatch.providerFor(args[1]);
                System.out.println("provider=NO-THROW");
            } catch (IllegalArgumentException bad) {
                System.out.println("provider=ERR");
            }
            return;
        }
        if (scene.equals("localzero")) {
            System.out.println("route=" + ModeDispatch.route(
                    CloudSpec.MODE_LOCAL,
                    "What is the capital of France?"));
            System.out.println("route=" + ModeDispatch.route(
                    CloudSpec.MODE_LOCAL,
                    "What's the current battery level?"));
            FakeRunner runner = new FakeRunner();
            FakeNet idle = new FakeNet();
            AskFlow.ask(runner, "What is the capital of France?",
                    new Nop());
            System.out.print(runner.log.toString());
            System.out.println("net=" + idle.count);
            return;
        }
        if (scene.equals("buildroute")) {
            CloudRequest.WireRequest meta = CloudRequest.build(
                    CloudSpec.META, "DUMMY-META-KEY",
                    "muse-spark-1.3", "SYS", "Q?", 4096);
            CloudRequest.WireRequest open = CloudRequest.build(
                    CloudSpec.BASETEN, "DUMMY-BASETEN-KEY",
                    "meta-models/muse-spark-open-1026", "SYS",
                    "Q?", 4096);
            dumpReq(meta);
            dumpReq(open);
            try {
                CloudRequest.build(CloudSpec.META,
                        "DUMMY-META-KEY", "other-model", "SYS",
                        "Q?", 4096);
                System.out.println("custom=OK");
            } catch (CloudRequest.Refused refused) {
                System.out.println("custom=" + refused.code);
            }
            try {
                CloudRequest.build(CloudSpec.META,
                        "DUMMY-META-KEY", "", "SYS", "Q?",
                        4096);
                System.out.println("refused-empty=NO-THROW");
            } catch (CloudRequest.Refused refused) {
                System.out.println(
                        "refused-empty=" + refused.code);
            }
            StringBuilder longId = new StringBuilder();
            for (int i = 0; i < 101; i++) {
                longId.append("a");
            }
            try {
                CloudRequest.build(CloudSpec.META,
                        "DUMMY-META-KEY", longId.toString(),
                        "SYS", "Q?", 4096);
                System.out.println("refused-long=NO-THROW");
            } catch (CloudRequest.Refused refused) {
                System.out.println(
                        "refused-long=" + refused.code);
            }
            try {
                CloudRequest.build(CloudSpec.META,
                        "DUMMY-META-KEY", "bad model!", "SYS",
                        "Q?", 4096);
                System.out.println("refused-bad=NO-THROW");
            } catch (CloudRequest.Refused refused) {
                System.out.println(
                        "refused-bad=" + refused.code);
            }
            try {
                CloudRequest.build(CloudSpec.BASETEN,
                        "DUMMY-BASETEN-KEY", "other-model",
                        "SYS", "Q?", 4096);
                System.out.println("refused-open=NO-THROW");
            } catch (CloudRequest.Refused refused) {
                System.out.println(
                        "refused-open=" + refused.code);
            }
            try {
                CloudRequest.build(CloudSpec.META,
                        "DUMMY-META-KEY", "muse-spark-1.3",
                        "SYS", "Q?", 4097);
                System.out.println("refused-out=NO-THROW");
            } catch (CloudRequest.Refused refused) {
                System.out.println(
                        "refused-out=" + refused.code);
            }
            StringBuilder big = new StringBuilder();
            for (int i = 0; i < 9000; i++) {
                big.append("s?");
            }
            try {
                CloudRequest.build(CloudSpec.META,
                        "DUMMY-META-KEY", "muse-spark-1.3",
                        big.toString(), "Q?", 4096);
                System.out.println("refused-body=NO-THROW");
            } catch (CloudRequest.Refused refused) {
                System.out.println(
                        "refused-body=" + refused.code);
            }
            try {
                CloudRequest.build(CloudSpec.META, "",
                        "muse-spark-1.3", "SYS", "Q?", 4096);
                System.out.println("refused-nokey=NO-THROW");
            } catch (CloudRequest.Refused refused) {
                System.out.println(
                        "refused-nokey=" + refused.code);
            }
            return;
        }
        if (scene.equals("qlimits")) {
            qlimits(args[1]);
            return;
        }
        if (scene.equals("errors")) {
            errorRows();
            return;
        }
        if (scene.equals("askok")) {
            KeyStorage.Memory keys = new KeyStorage.Memory();
            keys.save(CloudSpec.META, "DUMMY-META-KEY");
            FakeNet net = new FakeNet();
            net.script.add(new NetworkLayer.Response(200,
                    reply("Hi", "stop", true), -1, false));
            CloudCb cb = new CloudCb();
            ManualClock clock = new ManualClock();
            RecSleeper sleeper = new RecSleeper();
            CloudAsk.ask(CloudSpec.META, "muse-spark-1.3",
                    "SYS", "Q?", keys, net, clock, sleeper,
                    cb);
            System.out.println("event=" + cb.event);
            System.out.println("net=" + net.count);
            System.out.println(
                    "timeout0=" + net.timeouts.get(0));
            CloudRequest.WireRequest seen =
                    net.seen.get(0);
            System.out.println("host=" + seen.host);
            System.out.println("auth=" + seen.authHeader);
            System.out.println("sysin="
                    + seen.body.contains("SYS"));
            System.out.println("qin="
                    + seen.body.contains("Q?"));
            System.out.println("maxtok=" + seen.body.contains(
                    "\\"max_completion_tokens\\":4096"));
            return;
        }
        if (scene.equals("models")) {
            modelsAsk();
            return;
        }
        if (scene.equals("trunc")) {
            KeyStorage.Memory keys = new KeyStorage.Memory();
            keys.save(CloudSpec.META, "DUMMY-META-KEY");
            FakeNet net = new FakeNet();
            net.script.add(new NetworkLayer.Response(200,
                    reply("Hi", "stop", true), -1, true));
            CloudCb cb = new CloudCb();
            CloudAsk.ask(CloudSpec.META, "muse-spark-1.3",
                    "SYS", "Q?", keys, net, new ManualClock(),
                    new RecSleeper(), cb);
            System.out.println("event=" + cb.event);
            return;
        }
        if (scene.equals("askretry")) {
            askRetry(args[1]);
            return;
        }
        if (scene.equals("busy")) {
            busyAsk();
            return;
        }
        if (scene.equals("slowsend")) {
            slowSend();
            return;
        }
        if (scene.equals("trickle")) {
            trickleRead();
            return;
        }
        if (scene.equals("latewait")) {
            lateWait();
            return;
        }
        if (scene.equals("timeoutthenok")) {
            timeoutThenOk();
            return;
        }
        if (scene.equals("cancelclose")) {
            cancelClose();
            return;
        }
        if (scene.equals("stallread")) {
            stallRead();
            return;
        }
        if (scene.equals("stallheaders")) {
            stallHeaders();
            return;
        }
        if (scene.equals("stallwrite")) {
            stallWrite();
            return;
        }
        if (scene.equals("stallgate")) {
            stallGate();
            return;
        }
        if (scene.equals("stillclosing")) {
            stillClosing();
            return;
        }
        if (scene.equals("keyfail")) {
            KeyStorage.Memory keys = new KeyStorage.Memory();
            keys.setFailOnSave(true);
            try {
                keys.save(CloudSpec.META, args[1]);
                System.out.println("saved=YES");
            } catch (Exception failed) {
                System.out.println("saved=NO");
            }
            System.out.println(
                    "load=" + (keys.load(CloudSpec.META)
                            == null));
            FakeNet net = new FakeNet();
            CloudCb cb = new CloudCb();
            CloudAsk.ask(CloudSpec.META, "muse-spark-1.3",
                    "SYS", "Q?", keys, net, new ManualClock(),
                    new RecSleeper(), cb);
            System.out.println("event=" + cb.event);
            System.out.println("net=" + net.count);
            return;
        }
        if (scene.equals("privacy")) {
            KeyStorage.Memory keys = new KeyStorage.Memory();
            keys.save(CloudSpec.META, "DUMMY-META-KEY");
            FakeNet net = new FakeNet();
            net.script.add(new NetworkLayer.Response(200,
                    reply("Hi", "stop", false), -1, false));
            CloudAsk.ask(CloudSpec.META, "muse-spark-1.3",
                    "SYS", "CURRENT-Q-22", keys, net,
                    new ManualClock(), new RecSleeper(),
                    new CloudCb());
            System.out.println(
                    "body=" + net.seen.get(0).body);
            return;
        }
        if (scene.equals("redirects")) {
            FakeConn conn = new FakeConn();
            HttpNetworkLayer.setup(conn, 5000);
            System.out.println("follow=" + conn.follow
                    + "|connect=" + conn.connectTo + "|read="
                    + conn.readTo);
            Map<String, java.util.List<String>> three =
                    new HashMap<String,
                            java.util.List<String>>();
            three.put("Retry-After",
                    Arrays.asList("3"));
            Map<String, java.util.List<String>> bad =
                    new HashMap<String,
                            java.util.List<String>>();
            bad.put("retry-after", Arrays.asList("abc"));
            System.out.println("after3="
                    + HttpNetworkLayer.retryAfter(three));
            System.out.println("afterbad="
                    + HttpNetworkLayer.retryAfter(bad));
            System.out.println("afternull="
                    + HttpNetworkLayer.retryAfter(null));
            return;
        }
        if (scene.equals("keyops")) {
            KeyStorage.Memory keys = new KeyStorage.Memory();
            keys.save(CloudSpec.META, "DUMMY-KEY-XY12");
            System.out.println(
                    "four=" + keys.lastFour(CloudSpec.META));
            keys.delete(CloudSpec.META);
            System.out.println("gone="
                    + (keys.load(CloudSpec.META) == null)
                    + "|"
                    + keys.lastFour(CloudSpec.META).isEmpty());
            return;
        }
        if (scene.equals("labels")) {
            System.out.println("a=" + ModeLabel.label(
                    CloudSpec.MODE_META, "muse-spark-1.3"));
            System.out.println("b=" + ModeLabel.label(
                    CloudSpec.MODE_OPEN,
                    "meta-models/muse-spark-open-1026"));
            System.out.println("c=" + ModeLabel.label(
                    CloudSpec.MODE_LOCAL, "app"));
            return;
        }
        if (scene.equals("spec")) {
            System.out.println("metahost=" + CloudSpec.META_HOST);
            System.out.println("metapath=" + CloudSpec.META_PATH);
            System.out.println(
                    "metafield=" + CloudSpec.META_TOKEN_FIELD);
            System.out.println(
                    "basehost=" + CloudSpec.BASETEN_HOST);
            System.out.println(
                    "basepath=" + CloudSpec.BASETEN_PATH);
            System.out.println(
                    "basefield=" + CloudSpec.BASETEN_TOKEN_FIELD);
            System.out.println("metamodel="
                    + CloudSpec.DEFAULT_META_MODEL);
            System.out.println(
                    "openmodel=" + CloudSpec.OPEN_WEIGHT_MODEL);
            System.out.println(
                    "chars=" + CloudSpec.QUESTION_MAX_CHARS);
            System.out.println(
                    "bytes=" + CloudSpec.QUESTION_MAX_BYTES);
            System.out.println(
                    "body=" + CloudSpec.BODY_MAX_BYTES);
            System.out.println(
                    "out=" + CloudSpec.OUTPUT_MAX_TOKENS);
            System.out.println(
                    "reply=" + CloudSpec.REPLY_MAX_BYTES);
            System.out.println(
                    "timeout=" + CloudSpec.TIMEOUT_MS);
            System.out.println(
                    "modellen=" + CloudSpec.MODEL_ID_MAX_LEN);
            System.out.println(
                    "localblurb=" + CloudSpec.LOCAL_BLURB);
            System.out.println(
                    "cloudblurb=" + CloudSpec.CLOUD_BLURB);
            return;
        }
        System.out.println("ERROR unknown scene");
        System.exit(2);
    }

    static void dumpReq(CloudRequest.WireRequest req) {
        int sys = count(req.body, "\\"role\\":\\"system\\"");
        int user = count(req.body, "\\"role\\":\\"user\\"");
        String field = req.body.contains(
                "\\"max_completion_tokens\\"")
                        ? "max_completion_tokens"
                        : (req.body.contains("\\"max_tokens\\"")
                                ? "max_tokens" : "?");
        System.out.println("req|" + req.provider + "|"
                + req.host + "|" + req.path + "|"
                + req.authHeader + "|" + req.model + "|"
                + field + "|sys" + sys + "|user" + user);
    }

    static int count(String hay, String needle) {
        int n = 0;
        int i = 0;
        while (true) {
            i = hay.indexOf(needle, i);
            if (i < 0) {
                return n;
            }
            n++;
            i += needle.length();
        }
    }

    static void errorRows() {
        row("ok-stop", CloudSpec.META, "muse-spark-1.3", 200,
                reply("Hi", "stop", true), -1);
        row("ok-length", CloudSpec.META, "muse-spark-1.3", 200,
                reply("Hi", "length", true), -1);
        row("empty", CloudSpec.META, "muse-spark-1.3", 200,
                reply("", "stop", false), -1);
        row("malformed", CloudSpec.META, "muse-spark-1.3", 200,
                "not json", -1);
        row("401", CloudSpec.META, "muse-spark-1.3", 401,
                "{\\"error\\":\\"SECRET-BODY-77\\"}", -1);
        row("403", CloudSpec.BASETEN,
                "meta-models/muse-spark-open-1026", 403,
                "{\\"error\\":\\"SECRET-BODY-77\\"}", -1);
        row("402", CloudSpec.META, "muse-spark-1.3", 402,
                "{\\"error\\":\\"SECRET-BODY-77\\"}", -1);
        row("billing", CloudSpec.META, "muse-spark-1.3", 400,
                "{\\"error\\":\\"billing over SECRET-BODY-77\\"}",
                -1);
        row("quota400", CloudSpec.META, "muse-spark-1.3", 400,
                "{\\"error\\":\\"quota exhausted SECRET-BODY-77\\"}",
                -1);
        row("quota429", CloudSpec.META, "muse-spark-1.3", 429,
                "{\\"error\\":\\"quota exhausted SECRET-BODY-77\\"}",
                3);
        row("model404", CloudSpec.META, "muse-spark-1.3", 404,
                "model muse-spark-1.3 gone SECRET-BODY-77",
                -1);
        row("plain404", CloudSpec.META, "muse-spark-1.3", 404,
                "nothing here SECRET-BODY-77", -1);
        row("429-short", CloudSpec.META, "muse-spark-1.3", 429,
                "{\\"error\\":\\"SECRET-BODY-77\\"}", 3);
        row("429-long", CloudSpec.META, "muse-spark-1.3", 429,
                "{\\"error\\":\\"SECRET-BODY-77\\"}", 30);
        row("429-none", CloudSpec.META, "muse-spark-1.3", 429,
                "{\\"error\\":\\"SECRET-BODY-77\\"}", -1);
        row("500", CloudSpec.META, "muse-spark-1.3", 500,
                "{\\"error\\":\\"SECRET-BODY-77\\"}", -1);
    }

    static void row(String id, String provider, String model,
            int status, String body, int after) {
        Json.Reply reply = Json.parseReply(body);
        ErrorPolicy.Decision done = ErrorPolicy.decide(
                provider, model, status, body, reply, after);
        String kind = done.answer ? "answer"
                : (done.retry ? "retry" : "fail");
        System.out.println("row|" + id + "|" + kind + "|"
                + done.message + "|" + done.waitSec);
    }

    static void askRetry(String sub) throws Exception {
        KeyStorage.Memory keys = new KeyStorage.Memory();
        keys.save(CloudSpec.META, "DUMMY-META-KEY");
        FakeNet net = new FakeNet();
        if (sub.equals("short")) {
            net.script.add(new NetworkLayer.Response(429,
                    "{\\"error\\":\\"slow\\"}", 3, false));
            net.script.add(new NetworkLayer.Response(200,
                    reply("Hi", "stop", false), -1, false));
        } else if (sub.equals("long")) {
            net.script.add(new NetworkLayer.Response(429,
                    "{\\"error\\":\\"slow\\"}", 30, false));
        } else if (sub.equals("default")) {
            net.script.add(new NetworkLayer.Response(429,
                    "{\\"error\\":\\"slow\\"}", -1, false));
            net.script.add(new NetworkLayer.Response(200,
                    reply("Hi", "stop", false), -1, false));
        } else if (sub.equals("err5")) {
            net.script.add(new NetworkLayer.Response(500,
                    "{\\"error\\":\\"down\\"}", -1, false));
            net.script.add(new NetworkLayer.Response(200,
                    reply("Hi", "stop", false), -1, false));
        } else if (sub.equals("timeout")) {
            net.throwsIo.add(Boolean.TRUE);
            net.script.add(new NetworkLayer.Response(200,
                    reply("Hi", "stop", false), -1, false));
        } else if (sub.equals("err5twice")) {
            net.script.add(new NetworkLayer.Response(500,
                    "{\\"error\\":\\"down\\"}", -1, false));
            net.script.add(new NetworkLayer.Response(500,
                    "{\\"error\\":\\"down\\"}", -1, false));
            net.script.add(new NetworkLayer.Response(200,
                    reply("Hi", "stop", false), -1, false));
        } else if (sub.equals("fail402")) {
            net.script.add(new NetworkLayer.Response(402,
                    "{\\"error\\":\\"pay\\"}", -1, false));
            net.script.add(new NetworkLayer.Response(200,
                    reply("Hi", "stop", false), -1, false));
        } else if (sub.equals("failquota")) {
            net.script.add(new NetworkLayer.Response(429,
                    "{\\"error\\":\\"quota exhausted\\"}", 3,
                    false));
            net.script.add(new NetworkLayer.Response(200,
                    reply("Hi", "stop", false), -1, false));
        } else if (sub.equals("waitclock")) {
            ManualClock wclock = new ManualClock();
            RecSleeper wsleeper = new RecSleeper();
            wsleeper.clock = wclock;
            net.script.add(new NetworkLayer.Response(429,
                    "{\\"error\\":\\"slow\\"}", 3, false));
            net.script.add(new NetworkLayer.Response(200,
                    reply("Hi", "stop", false), -1, false));
            CloudCb wcb = new CloudCb();
            CloudAsk.ask(CloudSpec.META, "muse-spark-1.3",
                    "SYS", "Q?", keys, net, wclock,
                    wsleeper, wcb);
            System.out.println("event=" + wcb.event);
            System.out.println("net=" + net.count);
            System.out.println("sleeps=" + wsleeper.sleeps);
            System.out.println("timeouts=" + net.timeouts);
            return;
        } else if (sub.equals("deadline")) {
            ManualClock slow = new ManualClock();
            net.ticker = slow;
            net.script.add(new NetworkLayer.Response(500,
                    "{\\"error\\":\\"down\\"}", -1, false));
            net.script.add(new NetworkLayer.Response(200,
                    reply("Hi", "stop", false), -1, false));
            CloudCb early = new CloudCb();
            RecSleeper rest = new RecSleeper();
            CloudAsk.ask(CloudSpec.META, "muse-spark-1.3",
                    "SYS", "Q?", keys, net, slow, rest,
                    early);
            System.out.println("event=" + early.event);
            System.out.println("net=" + net.count);
            System.out.println("timeouts=" + net.timeouts);
            return;
        }
        CloudCb cb = new CloudCb();
        RecSleeper sleeper = new RecSleeper();
        ManualClock clock = new ManualClock();
        CloudAsk.ask(CloudSpec.META, "muse-spark-1.3", "SYS",
                "Q?", keys, net, clock, sleeper, cb);
        System.out.println("event=" + cb.event);
        System.out.println("net=" + net.count);
        System.out.println("sleeps=" + sleeper.sleeps);
        boolean same = true;
        for (CloudRequest.WireRequest seen : net.seen) {
            same = same && seen.host.equals(CloudSpec.META_HOST)
                    && seen.model.equals("muse-spark-1.3")
                    && seen.authHeader
                            .equals("Bearer DUMMY-META-KEY");
        }
        System.out.println("same=" + same);
        System.out.println("timeouts=" + net.timeouts);
    }

    static void qlimits(String sub) throws Exception {
        KeyStorage.Memory keys = new KeyStorage.Memory();
        keys.save(CloudSpec.META, "DUMMY-META-KEY");
        String q;
        if (sub.equals("ok2000")) {
            StringBuilder out = new StringBuilder();
            for (int i = 0; i < 2000; i++) {
                out.appendCodePoint(0x1F600);
            }
            q = out.toString();
        } else if (sub.equals("over2001")) {
            StringBuilder out = new StringBuilder();
            for (int i = 0; i < 2001; i++) {
                out.append("a");
            }
            q = out.toString();
        } else if (sub.equals("overemoji")) {
            StringBuilder out = new StringBuilder();
            for (int i = 0; i < 2001; i++) {
                out.appendCodePoint(0x1F600);
            }
            q = out.toString();
        } else if (sub.equals("over8001")) {
            StringBuilder out = new StringBuilder();
            for (int i = 0; i < 8001; i++) {
                out.append("a");
            }
            q = out.toString();
        } else if (sub.equals("bigbody")) {
            StringBuilder sys = new StringBuilder();
            for (int i = 0; i < 9000; i++) {
                sys.append("s?");
            }
            FakeNet net = new FakeNet();
            net.script.add(new NetworkLayer.Response(200,
                    reply("Hi", "stop", false), -1, false));
            CloudCb cb = new CloudCb();
            CloudAsk.ask(CloudSpec.META, "muse-spark-1.3",
                    sys.toString(), "Q?", keys, net,
                    new ManualClock(), new RecSleeper(), cb);
            System.out.println("event=" + cb.event);
            System.out.println("net=" + net.count);
            return;
        } else {
            q = sub;
        }
        FakeNet net = new FakeNet();
        net.script.add(new NetworkLayer.Response(200,
                reply("Hi", "stop", false), -1, false));
        CloudCb cb = new CloudCb();
        CloudAsk.ask(CloudSpec.META, "muse-spark-1.3", "SYS",
                q, keys, net, new ManualClock(),
                new RecSleeper(), cb);
        System.out.println("event=" + cb.event);
        System.out.println("net=" + net.count);
        System.out.println("chars="
                + CloudRequest.codePoints(q) + "|bytes="
                + CloudRequest.utf8Bytes(q));
    }

    static void modelsAsk() throws Exception {
        KeyStorage.Memory keys = new KeyStorage.Memory();
        keys.save(CloudSpec.META, "DUMMY-META-KEY");
        keys.save(CloudSpec.BASETEN, "DUMMY-BASETEN-KEY");
        FakeNet net = new FakeNet();
        net.script.add(new NetworkLayer.Response(200,
                reply("Hi", "stop", false), -1, false));
        CloudCb custom = new CloudCb();
        CloudAsk.ask(CloudSpec.META, "muse-spark-2.0", "SYS",
                "Q?", keys, net, new ManualClock(),
                new RecSleeper(), custom);
        System.out.println("custom=" + custom.event);
        CloudCb empty = new CloudCb();
        CloudAsk.ask(CloudSpec.META, "", "SYS", "Q?", keys,
                net, new ManualClock(), new RecSleeper(),
                empty);
        System.out.println("empty=" + empty.event);
        StringBuilder longId = new StringBuilder();
        for (int i = 0; i < 101; i++) {
            longId.append("a");
        }
        CloudCb tooLong = new CloudCb();
        CloudAsk.ask(CloudSpec.META, longId.toString(), "SYS",
                "Q?", keys, net, new ManualClock(),
                new RecSleeper(), tooLong);
        System.out.println("toolong=" + tooLong.event);
        CloudCb badChars = new CloudCb();
        CloudAsk.ask(CloudSpec.META, "bad model!", "SYS", "Q?",
                keys, net, new ManualClock(), new RecSleeper(),
                badChars);
        System.out.println("badchars=" + badChars.event);
        CloudCb openOk = new CloudCb();
        CloudAsk.ask(CloudSpec.BASETEN,
                "meta-models/muse-spark-open-1026", "SYS",
                "Q?", keys, net, new ManualClock(),
                new RecSleeper(), openOk);
        System.out.println("openok=" + openOk.event);
        CloudCb openBad = new CloudCb();
        CloudAsk.ask(CloudSpec.BASETEN, "other-model", "SYS",
                "Q?", keys, net, new ManualClock(),
                new RecSleeper(), openBad);
        System.out.println("openbad=" + openBad.event);
        System.out.println("net=" + net.count);
    }

    static void busyAsk() throws Exception {
        final KeyStorage.Memory keys = new KeyStorage.Memory();
        keys.save(CloudSpec.META, "DUMMY-META-KEY");
        final CountDownLatch entered =
                new CountDownLatch(1);
        final CountDownLatch release =
                new CountDownLatch(1);
        final int[] netCount = new int[1];
        NetworkLayer blocking = new NetworkLayer() {
            public Response send(
                    CloudRequest.WireRequest req,
                    int timeoutMs)
                    throws java.io.IOException {
                netCount[0]++;
                if (netCount[0] == 1) {
                    entered.countDown();
                    try {
                        release.await();
                    } catch (InterruptedException stop) {
                        throw new java.io.IOException(
                                "stopped");
                    }
                }
                return new Response(200,
                        reply("Hi", "stop", false), -1,
                        false);
            }
        };
        final CloudCb first = new CloudCb();
        Thread worker = new Thread(new Runnable() {
            public void run() {
                CloudAsk.ask(CloudSpec.META,
                        "muse-spark-1.3", "SYS", "Q?",
                        keys, blocking, new ManualClock(),
                        new RecSleeper(), first);
            }
        });
        worker.start();
        entered.await();
        CloudCb second = new CloudCb();
        CloudAsk.ask(CloudSpec.META, "muse-spark-1.3", "SYS",
                "Q2?", keys, blocking, new ManualClock(),
                new RecSleeper(), second);
        System.out.println("second=" + second.event);
        System.out.println("netDuring=" + netCount[0]);
        release.countDown();
        worker.join();
        System.out.println("first=" + first.event);
        System.out.println("net=" + netCount[0]);
    }

    static void slowSend() throws Exception {
        KeyStorage.Memory keys = new KeyStorage.Memory();
        keys.save(CloudSpec.META, "DUMMY-META-KEY");
        FakeNet net = new FakeNet();
        ManualClock clock = new ManualClock();
        net.ticker = clock;
        net.advances.add(70000L);
        net.script.add(new NetworkLayer.Response(200,
                reply("SECRET-LATE", "stop", false), -1,
                false));
        CloudCb cb = new CloudCb();
        CloudAsk.ask(CloudSpec.META, "muse-spark-1.3",
                "SYS", "Q?", keys, net, clock,
                new RecSleeper(), cb);
        System.out.println("event=" + cb.event);
        System.out.println("net=" + net.count);
        System.out.println("timeouts=" + net.timeouts);
    }

    static void trickleRead() throws Exception {
        ManualClock clock = new ManualClock();
        long deadline = clock.nowMs() + CloudSpec.TIMEOUT_MS;
        Trickle slow = new Trickle(clock, 20000L, 100);
        FakeConn slowConn = new FakeConn();
        boolean threw = false;
        try {
            HttpNetworkLayer.readUpTo(slowConn, slow,
                    CloudSpec.REPLY_MAX_BYTES + 1, clock,
                    deadline);
        } catch (java.io.IOException timedOut) {
            threw = true;
        }
        System.out.println("threw=" + threw);
        System.out.println("closed=" + slow.closed);
        System.out.println("readto=" + slowConn.readTo);
        ManualClock fast = new ManualClock();
        long soon = fast.nowMs() + CloudSpec.TIMEOUT_MS;
        Trickle quick = new Trickle(fast, 1000L, 10);
        FakeConn fastConn = new FakeConn();
        byte[] got = HttpNetworkLayer.readUpTo(fastConn,
                quick, 100, fast, soon);
        System.out.println("got=" + got.length);
        System.out.println("fastclosed=" + quick.closed);
        System.out.println(
                "fastreadto=" + fastConn.readTo);
    }

    static void lateWait() throws Exception {
        KeyStorage.Memory keys = new KeyStorage.Memory();
        keys.save(CloudSpec.META, "DUMMY-META-KEY");
        FakeNet net = new FakeNet();
        ManualClock clock = new ManualClock();
        net.ticker = clock;
        net.advances.add(55000L);
        net.script.add(new NetworkLayer.Response(429,
                "{\\"error\\":\\"slow\\"}", 10, false));
        net.script.add(new NetworkLayer.Response(200,
                reply("Hi", "stop", false), -1, false));
        RecSleeper sleeper = new RecSleeper();
        sleeper.clock = clock;
        CloudCb cb = new CloudCb();
        CloudAsk.ask(CloudSpec.META, "muse-spark-1.3",
                "SYS", "Q?", keys, net, clock, sleeper, cb);
        System.out.println("event=" + cb.event);
        System.out.println("net=" + net.count);
        System.out.println("sleeps=" + sleeper.sleeps);
        System.out.println("timeouts=" + net.timeouts);
    }

    static void timeoutThenOk() throws Exception {
        KeyStorage.Memory keys = new KeyStorage.Memory();
        keys.save(CloudSpec.META, "DUMMY-META-KEY");
        FakeNet slow = new FakeNet();
        ManualClock clock = new ManualClock();
        slow.ticker = clock;
        slow.advances.add(70000L);
        slow.script.add(new NetworkLayer.Response(200,
                reply("Hi", "stop", false), -1, false));
        CloudCb first = new CloudCb();
        CloudAsk.ask(CloudSpec.META, "muse-spark-1.3",
                "SYS", "Q?", keys, slow, clock,
                new RecSleeper(), first);
        System.out.println("first=" + first.event);
        FakeNet fast = new FakeNet();
        fast.script.add(new NetworkLayer.Response(200,
                reply("Hi", "stop", false), -1, false));
        CloudCb second = new CloudCb();
        CloudAsk.ask(CloudSpec.META, "muse-spark-1.3",
                "SYS", "Q2?", keys, fast, new ManualClock(),
                new RecSleeper(), second);
        System.out.println("second=" + second.event);
        System.out.println("net=" + fast.count);
    }

    static void cancelClose() throws Exception {
        final KeyStorage.Memory keys = new KeyStorage.Memory();
        keys.save(CloudSpec.META, "DUMMY-META-KEY");
        final ManualClock clock = new ManualClock();
        final boolean[] running = new boolean[1];
        final int[] cancelled = new int[1];
        NetworkLayer stuck = new NetworkLayer() {
            public Response send(
                    CloudRequest.WireRequest req,
                    int timeoutMs)
                    throws java.io.IOException {
                running[0] = true;
                clock.now += 70000;
                throw new java.io.IOException("stuck");
            }

            public void cancel() {
                cancelled[0]++;
                running[0] = false;
            }
        };
        CloudCb first = new CloudCb();
        CloudAsk.ask(CloudSpec.META, "muse-spark-1.3", "SYS",
                "Q?", keys, stuck, clock, new RecSleeper(),
                first);
        System.out.println("first=" + first.event);
        System.out.println("cancelled=" + cancelled[0]);
        System.out.println("running=" + running[0]);
        FakeNet fast = new FakeNet();
        fast.script.add(new NetworkLayer.Response(200,
                reply("Hi", "stop", false), -1, false));
        CloudCb second = new CloudCb();
        CloudAsk.ask(CloudSpec.META, "muse-spark-1.3",
                "SYS", "Q2?", keys, fast, new ManualClock(),
                new RecSleeper(), second);
        System.out.println("second=" + second.event);
    }

    static java.net.ServerSocket loopbackServer()
            throws java.io.IOException {
        for (int i = 0; i < 10; i++) {
            java.net.ServerSocket server =
                    new java.net.ServerSocket(0, 8,
                            java.net.InetAddress.getByName(
                                    "127.0.0.1"));
            int port = server.getLocalPort();
            if (port != 8765 && port != 8080) {
                return server;
            }
            server.close();
        }
        throw new java.io.IOException("no clean port");
    }

    static void eatRequest(java.net.Socket s)
            throws java.io.IOException {
        java.io.InputStream in = s.getInputStream();
        java.io.ByteArrayOutputStream head =
                new java.io.ByteArrayOutputStream();
        int last4 = 0;
        while (true) {
            int b = in.read();
            if (b < 0) {
                return;
            }
            head.write(b);
            last4 = (last4 << 8) | b;
            if (last4 == 0x0D0A0D0A) {
                break;
            }
        }
        String headers =
                new String(head.toByteArray(), "UTF-8");
        int contentLen = 0;
        for (String line : headers.split("\\r\\n")) {
            if (line.toLowerCase().startsWith(
                    "content-length:")) {
                contentLen = Integer.parseInt(
                        line.substring(15).trim());
            }
        }
        for (int i = 0; i < contentLen; i++) {
            if (in.read() < 0) {
                break;
            }
        }
    }

    static void stallRead() throws Exception {
        final java.net.ServerSocket server =
                loopbackServer();
        final int port = server.getLocalPort();
        final long budgetMs = 2000;
        final boolean[] sawClose = new boolean[1];
        Thread srv = new Thread(new Runnable() {
            public void run() {
                try {
                    java.net.Socket first =
                            server.accept();
                    eatRequest(first);
                    Thread.sleep(budgetMs - 100);
                    java.io.OutputStream w =
                            first.getOutputStream();
                    String head = "HTTP/1.1 200 OK\\r\\n"
                            + "Content-Type: application/json"
                            + "\\r\\nContent-Length: 100\\r\\n"
                            + "Connection: close\\r\\n\\r\\n{";
                    w.write(head.getBytes("UTF-8"));
                    w.flush();
                    first.setSoTimeout(15000);
                    try {
                        int end = first.getInputStream()
                                .read();
                        sawClose[0] = (end < 0);
                    } catch (java.io.IOException early) {
                        sawClose[0] = false;
                    }
                    first.close();
                    java.net.Socket second =
                            server.accept();
                    eatRequest(second);
                    java.io.OutputStream w2 =
                            second.getOutputStream();
                    String body =
                            reply("Hi", "stop", false);
                    byte[] raw = body.getBytes("UTF-8");
                    String head2 = "HTTP/1.1 200 OK\\r\\n"
                            + "Content-Type: application/json"
                            + "\\r\\nContent-Length: "
                            + raw.length
                            + "\\r\\nConnection: close"
                            + "\\r\\n\\r\\n";
                    w2.write(head2.getBytes("UTF-8"));
                    w2.write(raw);
                    w2.flush();
                    second.close();
                } catch (Exception ignored) {
                }
            }
        });
        srv.setDaemon(true);
        srv.start();
        CloudRequest.WireRequest req = CloudRequest.build(
                CloudSpec.META, "DUMMY-META-KEY",
                "muse-spark-1.3", "SYS", "Q?", 4096);
        HttpNetworkLayer layer = new HttpNetworkLayer();
        java.net.URL url = new java.net.URL(
                "http://127.0.0.1:" + port
                + "/v1/chat/completions");
        long t0 = System.nanoTime();
        boolean threw = false;
        String thrown = "";
        try {
            layer.sendTo(req, (int) budgetMs, url);
        } catch (java.io.IOException failed) {
            threw = true;
            thrown = failed.getClass().getSimpleName();
        }
        long tookMs = (System.nanoTime() - t0) / 1000000L;
        System.out.println("threw=" + threw + "|" + thrown);
        System.out.println("tookMs=" + tookMs);
        System.out.println("closing1="
                + HttpNetworkLayer.closingCount());
        settleAndSecond(req, layer, url);
        srv.join(25000);
        System.out.println("sawclose=" + sawClose[0]);
        System.out.println("port=" + port);
        server.close();
    }

    static void stallHeaders() throws Exception {
        final java.net.ServerSocket server =
                loopbackServer();
        final int port = server.getLocalPort();
        final long budgetMs = 2000;
        final boolean[] sawClose = new boolean[1];
        Thread srv = new Thread(new Runnable() {
            public void run() {
                try {
                    java.net.Socket first =
                            server.accept();
                    eatRequest(first);
                    first.setSoTimeout(15000);
                    try {
                        int end = first.getInputStream()
                                .read();
                        sawClose[0] = (end < 0);
                    } catch (java.io.IOException early) {
                        sawClose[0] = false;
                    }
                    first.close();
                    java.net.Socket second =
                            server.accept();
                    eatRequest(second);
                    java.io.OutputStream w2 =
                            second.getOutputStream();
                    String body =
                            reply("Hi", "stop", false);
                    byte[] raw = body.getBytes("UTF-8");
                    String head2 = "HTTP/1.1 200 OK\\r\\n"
                            + "Content-Type: application/json"
                            + "\\r\\nContent-Length: "
                            + raw.length
                            + "\\r\\nConnection: close"
                            + "\\r\\n\\r\\n";
                    w2.write(head2.getBytes("UTF-8"));
                    w2.write(raw);
                    w2.flush();
                    second.close();
                } catch (Exception ignored) {
                }
            }
        });
        srv.setDaemon(true);
        srv.start();
        CloudRequest.WireRequest req = CloudRequest.build(
                CloudSpec.META, "DUMMY-META-KEY",
                "muse-spark-1.3", "SYS", "Q?", 4096);
        HttpNetworkLayer layer = new HttpNetworkLayer();
        java.net.URL url = new java.net.URL(
                "http://127.0.0.1:" + port
                + "/v1/chat/completions");
        long t0 = System.nanoTime();
        boolean threw = false;
        String thrown = "";
        try {
            layer.sendTo(req, (int) budgetMs, url);
        } catch (java.io.IOException failed) {
            threw = true;
            thrown = failed.getClass().getSimpleName();
        }
        long tookMs = (System.nanoTime() - t0) / 1000000L;
        System.out.println("threw=" + threw + "|" + thrown);
        System.out.println("tookMs=" + tookMs);
        System.out.println("closing1="
                + HttpNetworkLayer.closingCount());
        settleAndSecond(req, layer, url);
        srv.join(25000);
        System.out.println("sawclose=" + sawClose[0]);
        System.out.println("port=" + port);
        server.close();
    }

    static void waitQuiescent(long budgetMs)
            throws Exception {
        long end = System.nanoTime()
                + budgetMs * 1000000L;
        while (HttpNetworkLayer.closingCount() > 0) {
            if (System.nanoTime() >= end) {
                throw new java.io.IOException(
                        "worker never exited");
            }
            Thread.sleep(50);
        }
    }

    static void settleAndSecond(
            CloudRequest.WireRequest req,
            HttpNetworkLayer layer, java.net.URL url)
            throws Exception {
        try {
            long t1 = System.nanoTime();
            NetworkLayer.Response fast =
                    layer.sendTo(req, 10000, url);
            long took2Ms =
                    (System.nanoTime() - t1) / 1000000L;
            System.out.println("gate1=allowed");
            System.out.println("second=" + fast.status + "|"
                    + fast.body.contains("Hi"));
            System.out.println("took2Ms=" + took2Ms);
        } catch (java.io.IOException busy) {
            if (!HttpNetworkLayer.STILL_CLOSING.equals(
                    busy.getMessage())) {
                throw busy;
            }
            System.out.println("gate1=rejected|"
                    + busy.getMessage());
            long w0 = System.nanoTime();
            waitQuiescent(15000);
            long closedMs =
                    (System.nanoTime() - w0) / 1000000L;
            System.out.println("closedMs=" + closedMs);
            long t1 = System.nanoTime();
            NetworkLayer.Response fast =
                    layer.sendTo(req, 10000, url);
            long took2Ms =
                    (System.nanoTime() - t1) / 1000000L;
            System.out.println("second=" + fast.status + "|"
                    + fast.body.contains("Hi"));
            System.out.println("took2Ms=" + took2Ms);
        }
    }

    static void stallWrite() throws Exception {
        final java.net.ServerSocket server =
                loopbackServer();
        final int port = server.getLocalPort();
        final long budgetMs = 2000;
        Thread srv = new Thread(new Runnable() {
            public void run() {
                try {
                    java.net.Socket first =
                            server.accept();
                    // Never read: the client's request-body
                    // write must stall once the buffers fill.
                    // A socket write has no timeout, so only
                    // the caller's deadline can stop it.
                    Thread.sleep(4000);
                    first.close();
                    java.net.Socket second =
                            server.accept();
                    eatRequest(second);
                    java.io.OutputStream w2 =
                            second.getOutputStream();
                    String body =
                            reply("Hi", "stop", false);
                    byte[] raw = body.getBytes("UTF-8");
                    String head2 = "HTTP/1.1 200 OK\\r\\n"
                            + "Content-Type: application/json"
                            + "\\r\\nContent-Length: "
                            + raw.length
                            + "\\r\\nConnection: close"
                            + "\\r\\n\\r\\n";
                    w2.write(head2.getBytes("UTF-8"));
                    w2.write(raw);
                    w2.flush();
                    second.close();
                } catch (Exception ignored) {
                }
            }
        });
        srv.setDaemon(true);
        srv.start();
        StringBuilder big = new StringBuilder();
        for (int i = 0; i < 16 * 1024 * 1024; i++) {
            big.append('x');
        }
        HttpNetworkLayer layer = new HttpNetworkLayer();
        java.net.URL url = new java.net.URL(
                "http://127.0.0.1:" + port
                + "/v1/chat/completions");
        long t0 = System.nanoTime();
        boolean threw = false;
        String thrown = "";
        try {
            layer.sendBody(big.toString(),
                    "Bearer DUMMY-META-KEY",
                    (int) budgetMs, url);
        } catch (java.io.IOException failed) {
            threw = true;
            thrown = failed.getClass().getSimpleName();
        }
        long tookMs = (System.nanoTime() - t0) / 1000000L;
        System.out.println("threw=" + threw + "|" + thrown);
        System.out.println("tookMs=" + tookMs);
        System.out.println("closing1="
                + HttpNetworkLayer.closingCount());
        CloudRequest.WireRequest req = CloudRequest.build(
                CloudSpec.META, "DUMMY-META-KEY",
                "muse-spark-1.3", "SYS", "Q?", 4096);
        settleAndSecond(req, layer, url);
        srv.join(25000);
        System.out.println("port=" + port);
        server.close();
    }

    static void stallGate() throws Exception {
        final java.net.ServerSocket server =
                loopbackServer();
        final int port = server.getLocalPort();
        final long budgetMs = 5000;
        final boolean[] sawClose = new boolean[1];
        Thread srv = new Thread(new Runnable() {
            public void run() {
                try {
                    java.net.Socket first =
                            server.accept();
                    eatRequest(first);
                    java.io.OutputStream w =
                            first.getOutputStream();
                    String head = "HTTP/1.1 200 OK\\r\\n"
                            + "Content-Type: application/json"
                            + "\\r\\nContent-Length: 100\\r\\n"
                            + "Connection: close\\r\\n\\r\\n{";
                    w.write(head.getBytes("UTF-8"));
                    w.flush();
                    first.setSoTimeout(15000);
                    try {
                        int end = first.getInputStream()
                                .read();
                        sawClose[0] = (end < 0);
                    } catch (java.io.IOException early) {
                        sawClose[0] = false;
                    }
                    first.close();
                    java.net.Socket second =
                            server.accept();
                    eatRequest(second);
                    java.io.OutputStream w2 =
                            second.getOutputStream();
                    String body =
                            reply("Hi", "stop", false);
                    byte[] raw = body.getBytes("UTF-8");
                    String head2 = "HTTP/1.1 200 OK\\r\\n"
                            + "Content-Type: application/json"
                            + "\\r\\nContent-Length: "
                            + raw.length
                            + "\\r\\nConnection: close"
                            + "\\r\\n\\r\\n";
                    w2.write(head2.getBytes("UTF-8"));
                    w2.write(raw);
                    w2.flush();
                    second.close();
                } catch (Exception ignored) {
                }
            }
        });
        srv.setDaemon(true);
        srv.start();
        final CloudRequest.WireRequest req =
                CloudRequest.build(CloudSpec.META,
                        "DUMMY-META-KEY", "muse-spark-1.3",
                        "SYS", "Q?", 4096);
        final HttpNetworkLayer layer = new HttpNetworkLayer();
        final java.net.URL url = new java.net.URL(
                "http://127.0.0.1:" + port
                + "/v1/chat/completions");
        final boolean[] threw = new boolean[1];
        final String[] thrown = new String[1];
        final long[] took = new long[1];
        Thread caller = new Thread(new Runnable() {
            public void run() {
                long t0 = System.nanoTime();
                try {
                    layer.sendTo(req, (int) budgetMs, url);
                    thrown[0] = "";
                } catch (java.io.IOException failed) {
                    threw[0] = true;
                    thrown[0] =
                            failed.getClass().getSimpleName();
                }
                took[0] = (System.nanoTime() - t0)
                        / 1000000L;
            }
        });
        caller.start();
        Thread.sleep(500);
        System.out.println("closingMid="
                + HttpNetworkLayer.closingCount());
        caller.interrupt();
        caller.join(10000);
        System.out.println("alive=" + caller.isAlive());
        System.out.println(
                "threw=" + threw[0] + "|" + thrown[0]);
        System.out.println("tookMs=" + took[0]);
        System.out.println("closing1="
                + HttpNetworkLayer.closingCount());
        try {
            layer.sendTo(req, 10000, url);
            System.out.println("gate1=allowed");
        } catch (java.io.IOException busy) {
            System.out.println("gate1=rejected|"
                    + busy.getMessage());
        }
        long w0 = System.nanoTime();
        waitQuiescent(15000);
        long closedMs = (System.nanoTime() - w0) / 1000000L;
        System.out.println("closedMs=" + closedMs);
        long t1 = System.nanoTime();
        NetworkLayer.Response fast =
                layer.sendTo(req, 10000, url);
        long took2Ms = (System.nanoTime() - t1) / 1000000L;
        System.out.println("second=" + fast.status + "|"
                + fast.body.contains("Hi"));
        System.out.println("took2Ms=" + took2Ms);
        srv.join(25000);
        System.out.println("sawclose=" + sawClose[0]);
        System.out.println("port=" + port);
        server.close();
    }

    static void stillClosing() throws Exception {
        KeyStorage.Memory keys = new KeyStorage.Memory();
        keys.save(CloudSpec.META, "DUMMY-META-KEY");
        final int[] count = new int[1];
        NetworkLayer busy = new NetworkLayer() {
            public Response send(
                    CloudRequest.WireRequest req,
                    int timeoutMs)
                    throws java.io.IOException {
                count[0]++;
                throw new java.io.IOException(
                        HttpNetworkLayer.STILL_CLOSING);
            }
        };
        CloudCb cb = new CloudCb();
        CloudAsk.ask(CloudSpec.META, "muse-spark-1.3",
                "SYS", "Q?", keys, busy, new ManualClock(),
                new RecSleeper(), cb);
        System.out.println("event=" + cb.event);
        System.out.println("net=" + count[0]);
    }
}
"""


@workspace.needs_built_apps
class M4cHost(unittest.TestCase):
    """M4c app logic on a host JVM, with fakes, offline."""

    @classmethod
    def setUpClass(cls):
        if not JAVAC.exists() or not JAVA.exists():
            raise AssertionError("spike JDK missing under %s"
                                 % JAVAC.parent.parent.parent)
        cls.workdir = tempfile.mkdtemp(prefix="m4c-host-")
        build = subprocess.run(
            [str(JAVAC), "--release", "8", "-d", cls.workdir]
            + [str(APP_JAVA / name) for name in M4C_PURE],
            capture_output=True, text=True)
        if build.returncode != 0:
            raise AssertionError("javac failed: %s" % build.stderr)
        harness = os.path.join(cls.workdir, "FakeCloud.java")
        with open(harness, "w", encoding="utf-8") as handle:
            handle.write(FAKE_CLOUD)
        build_fake = subprocess.run(
            [str(JAVAC), "--release", "8", "-cp", cls.workdir,
             "-d", cls.workdir, harness],
            capture_output=True, text=True)
        if build_fake.returncode != 0:
            raise AssertionError(
                "javac fake failed: %s" % build_fake.stderr)

    def _java(self, *args):
        return subprocess.run(
            [str(JAVA), "-cp", self.workdir] + list(args),
            capture_output=True, text=True)

    def _scene(self, *args):
        done = self._java("com.stargate.assistant.FakeCloud",
                          *args)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def _lines(self, text, prefix):
        return [line[len(prefix):] for line in text.splitlines()
                if line.startswith(prefix)]

    def test_routes(self):
        out = self._scene("route", "Local",
                          "What is the capital of France?")
        self.assertEqual(self._lines(out, "route="), ["local"])
        out = self._scene("route", "Meta",
                          "What is the capital of France?")
        self.assertEqual(self._lines(out, "route="), ["cloud"])
        self.assertEqual(self._lines(out, "provider="), ["meta"])
        out = self._scene("route", "Meta open-weight",
                          "What is the capital of France?")
        self.assertEqual(self._lines(out, "route="), ["cloud"])
        self.assertEqual(self._lines(out, "provider="),
                         ["baseten"])

    def test_device_fact_routes_to_app_in_every_mode(self):
        for mode in ("Local", "Meta", "Meta open-weight"):
            out = self._scene(
                "route", mode, "What's the current battery level?")
            self.assertEqual(self._lines(out, "route="), ["app"],
                             mode)

    def test_unknown_mode_stays_local(self):
        out = self._scene("route", "Bogus",
                          "What is the capital of France?")
        self.assertEqual(self._lines(out, "route="), ["local"])
        out = self._scene("providererr", "Local")
        self.assertEqual(self._lines(out, "provider="), ["ERR"])

    def test_local_mode_makes_zero_requests(self):
        out = self._scene("localzero")
        routes = self._lines(out, "route=")
        self.assertEqual(routes, ["local", "app"])
        self.assertIn("reset", out)
        self.assertIn("generate start=0", out)
        self.assertEqual(self._lines(out, "net="), ["0"])

    def test_credential_routing(self):
        out = self._scene("buildroute")
        reqs = self._lines(out, "req|")
        self.assertEqual(len(reqs), 2)
        meta = reqs[0].split("|")
        self.assertEqual(meta[0:5],
                         ["meta", "api.meta.ai",
                          "/v1/chat/completions",
                          "Bearer " + DUMMY_META,
                          "muse-spark-1.3"])
        self.assertEqual(meta[5], "max_completion_tokens")
        self.assertNotIn(DUMMY_BASETEN, reqs[0])
        bw = reqs[1].split("|")
        self.assertEqual(bw[0:5],
                         ["baseten", "inference.baseten.co",
                          "/v1/chat/completions",
                          "Bearer " + DUMMY_BASETEN,
                          "meta-models/muse-spark-open-1026"])
        self.assertEqual(bw[5], "max_tokens")
        self.assertNotIn(DUMMY_META, reqs[1])
        for req in reqs:
            self.assertTrue(req.endswith("|sys1|user1"), req)

    def test_build_refusals(self):
        out = self._scene("buildroute")
        self.assertEqual(self._lines(out, "custom="), ["OK"])
        self.assertEqual(self._lines(out, "refused-empty="),
                         ["model"])
        self.assertEqual(self._lines(out, "refused-long="),
                         ["model"])
        self.assertEqual(self._lines(out, "refused-bad="),
                         ["model"])
        self.assertEqual(self._lines(out, "refused-open="),
                         ["model"])
        self.assertEqual(self._lines(out, "refused-out="),
                         ["output"])
        self.assertEqual(self._lines(out, "refused-body="),
                         ["body"])
        self.assertEqual(self._lines(out, "refused-nokey="),
                         ["no-key"])

    def test_question_limits_refused_before_network(self):
        out = self._scene("qlimits", "ok2000")
        self.assertTrue(
            self._lines(out, "event=")[0].startswith(
                "answer|Hi"), out)
        self.assertEqual(self._lines(out, "net="), ["1"])
        self.assertEqual(self._lines(out, "chars="),
                         ["2000|bytes=8000"])
        for sub, chars in (("over2001", "2001|bytes=2001"),
                           ("overemoji", "2001|bytes=8004"),
                           ("over8001", "8001|bytes=8001")):
            out = self._scene("qlimits", sub)
            self.assertEqual(
                self._lines(out, "event="),
                ["refused|Question too long to send."],
                sub)
            self.assertEqual(self._lines(out, "net="), ["0"],
                             sub)
            self.assertEqual(self._lines(out, "chars="),
                             [chars], sub)

    def test_body_over_16k_refused_before_network(self):
        out = self._scene("qlimits", "bigbody")
        self.assertEqual(
            self._lines(out, "event="),
            ["refused|Question too long to send."])
        self.assertEqual(self._lines(out, "net="), ["0"])

    def test_error_table(self):
        out = self._scene("errors")
        rows = {}
        for line in self._lines(out, "row|"):
            parts = line.split("|")
            rows[parts[0]] = (parts[1], parts[2],
                              int(parts[3]))
        self.assertEqual(rows["ok-stop"],
                         ("answer", "Hi", 0))
        self.assertEqual(rows["ok-length"],
                         ("answer", "Hi [incomplete]", 0))
        self.assertEqual(rows["empty"],
                         ("fail", "No usable answer", 0))
        self.assertEqual(rows["malformed"],
                         ("fail", "No usable answer", 0))
        self.assertEqual(rows["401"],
                         ("fail", "Key rejected by Meta", 0))
        self.assertEqual(rows["403"],
                         ("fail", "Key rejected by Baseten", 0))
        self.assertEqual(rows["402"],
                         ("fail", "Meta account needs payment",
                          0))
        self.assertEqual(rows["billing"],
                         ("fail", "Meta account needs payment",
                          0))
        self.assertEqual(rows["quota400"],
                         ("fail", "Meta account needs payment",
                          0))
        self.assertEqual(rows["quota429"],
                         ("fail", "Meta account needs payment",
                          0))
        self.assertEqual(rows["model404"],
                         ("fail", "Model not available", 0))
        self.assertEqual(rows["plain404"],
                         ("fail", "Meta not reachable", 0))
        self.assertEqual(rows["429-short"],
                         ("retry", "Too many requests, try later",
                          3))
        self.assertEqual(rows["429-long"],
                         ("fail", "Too many requests, try later",
                          0))
        self.assertEqual(rows["429-none"],
                         ("retry", "Too many requests, try later",
                          1))
        self.assertEqual(rows["500"],
                         ("retry", "Meta not reachable", 0))
        for _id, (_kind, message, _wait) in rows.items():
            self.assertNotIn("SECRET-BODY-77", message, _id)

    def test_ask_ok(self):
        out = self._scene("askok")
        self.assertEqual(
            self._lines(out, "event="),
            ["answer|Hi|false|false|10|5"])
        self.assertEqual(self._lines(out, "net="), ["1"])
        self.assertEqual(self._lines(out, "timeout0="),
                         ["60000"])
        self.assertEqual(self._lines(out, "host="),
                         ["api.meta.ai"])
        self.assertEqual(self._lines(out, "auth="),
                         ["Bearer " + DUMMY_META])
        self.assertEqual(self._lines(out, "sysin="), ["true"])
        self.assertEqual(self._lines(out, "qin="), ["true"])

    def _retry(self, sub):
        out = self._scene("askretry", sub)
        get = lambda prefix: self._lines(out, prefix)[0]
        return {"event": get("event="), "net": get("net="),
                "sleeps": get("sleeps="),
                "same": get("same="),
                "timeouts": get("timeouts=")}

    def test_retry_after_short_wait(self):
        got = self._retry("short")
        self.assertTrue(got["event"].startswith("answer|Hi"),
                        got)
        self.assertEqual(got["net"], "2")
        self.assertEqual(got["sleeps"], "[3000]")
        self.assertEqual(got["same"], "true")

    def test_retry_after_long_wait_refused(self):
        got = self._retry("long")
        self.assertEqual(
            got["event"], "error|Too many requests, try later")
        self.assertEqual(got["net"], "1")
        self.assertEqual(got["sleeps"], "[]")

    def test_retry_after_default_wait(self):
        got = self._retry("default")
        self.assertTrue(got["event"].startswith("answer|Hi"),
                        got)
        self.assertEqual(got["sleeps"], "[1000]")

    def test_retry_server_error(self):
        got = self._retry("err5")
        self.assertTrue(got["event"].startswith("answer|Hi"),
                        got)
        self.assertEqual(got["net"], "2")
        self.assertEqual(got["sleeps"], "[0]")
        self.assertEqual(got["same"], "true")

    def test_retry_timeout(self):
        got = self._retry("timeout")
        self.assertTrue(got["event"].startswith("answer|Hi"),
                        got)
        self.assertEqual(got["net"], "2")

    def test_second_retry_never_made(self):
        got = self._retry("err5twice")
        self.assertEqual(got["event"],
                         "error|Meta not reachable")
        self.assertEqual(got["net"], "2")

    def test_hard_errors_never_retried(self):
        for sub in ("fail402", "failquota"):
            got = self._retry(sub)
            self.assertEqual(
                got["event"],
                "error|Meta account needs payment", sub)
            self.assertEqual(got["net"], "1", sub)
            self.assertEqual(got["sleeps"], "[]", sub)

    def test_wait_counts_in_deadline(self):
        out = self._scene("askretry", "waitclock")
        get = lambda prefix: self._lines(out, prefix)[0]
        self.assertTrue(get("event=").startswith("answer|Hi"),
                        out)
        self.assertEqual(get("net="), "2")
        self.assertEqual(get("sleeps="), "[3000]")
        self.assertEqual(get("timeouts="), "[60000, 57000]")

    def test_second_question_while_running_refused(self):
        out = self._scene("busy")
        self.assertEqual(
            self._lines(out, "second="),
            ["refused|Another question is running."])
        self.assertEqual(self._lines(out, "netDuring="), ["1"])
        self.assertTrue(
            self._lines(out, "first=")[0].startswith(
                "answer|Hi"), out)
        self.assertEqual(self._lines(out, "net="), ["1"])

    def test_model_ids_refused_before_network(self):
        out = self._scene("models")
        self.assertTrue(
            self._lines(out, "custom=")[0].startswith(
                "answer|Hi"), out)
        self.assertEqual(
            self._lines(out, "empty="),
            ["refused|Model not available"])
        self.assertEqual(
            self._lines(out, "toolong="),
            ["refused|Model not available"])
        self.assertEqual(
            self._lines(out, "badchars="),
            ["refused|Model not available"])
        self.assertTrue(
            self._lines(out, "openok=")[0].startswith(
                "answer|Hi"), out)
        self.assertEqual(
            self._lines(out, "openbad="),
            ["refused|Model not available"])
        self.assertEqual(self._lines(out, "net="), ["2"])

    def test_keystore_failure(self):
        out = self._scene("keyfail", "DUMMY-KEY-22")
        self.assertEqual(self._lines(out, "saved="), ["NO"])
        self.assertEqual(self._lines(out, "load="), ["true"])
        self.assertEqual(
            self._lines(out, "event="),
            ["refused|Add your Meta key in settings first."])
        self.assertEqual(self._lines(out, "net="), ["0"])
        self.assertNotIn("DUMMY-KEY-22", out)
        for root, _dirs, names in os.walk(self.workdir):
            for name in names:
                if name.endswith(".java"):
                    continue
                path = os.path.join(root, name)
                with open(path, "rb") as handle:
                    self.assertNotIn(b"DUMMY-KEY-22",
                                     handle.read(), path)

    def test_privacy_no_history_sent(self):
        out = self._scene("privacy")
        body = self._lines(out, "body=")[0]
        self.assertIn("CURRENT-Q-22", body)
        self.assertNotIn("EARLIER", body)
        self.assertEqual(body.count('"role":"system"'), 1)
        self.assertEqual(body.count('"role":"user"'), 1)
        self.assertIn("SYS", body)

    def test_redirects_refused(self):
        out = self._scene("redirects")
        self.assertEqual(
            self._lines(out, "follow="),
            ["false|connect=5000|read=5000"])
        self.assertEqual(self._lines(out, "after3="), ["3"])
        self.assertEqual(self._lines(out, "afterbad="), ["-1"])
        self.assertEqual(self._lines(out, "afternull="), ["-1"])

    def test_key_ops(self):
        out = self._scene("keyops")
        self.assertEqual(self._lines(out, "four="), ["XY12"])
        self.assertEqual(self._lines(out, "gone="),
                         ["true|true"])

    def test_labels(self):
        out = self._scene("labels")
        self.assertEqual(
            self._lines(out, "a="), ["Meta · muse-spark-1.3"])
        self.assertEqual(
            self._lines(out, "b="),
            ["Meta open-weight · "
             "meta-models/muse-spark-open-1026"])
        self.assertEqual(self._lines(out, "c="), ["Local · app"])

    def test_spec_constants(self):
        out = self._scene("spec")
        get = lambda prefix: self._lines(out, prefix)[0]
        self.assertEqual(get("metahost="), "api.meta.ai")
        self.assertEqual(get("metapath="),
                         "/v1/chat/completions")
        self.assertEqual(get("metafield="),
                         "max_completion_tokens")
        self.assertEqual(get("basehost="),
                         "inference.baseten.co")
        self.assertEqual(get("basepath="),
                         "/v1/chat/completions")
        self.assertEqual(get("basefield="), "max_tokens")
        self.assertEqual(get("metamodel="), "muse-spark-1.3")
        self.assertEqual(get("openmodel="),
                         "meta-models/muse-spark-open-1026")
        self.assertEqual(get("chars="), "2000")
        self.assertEqual(get("bytes="), "8000")
        self.assertEqual(get("body="), "16384")
        self.assertEqual(get("out="), "4096")
        self.assertEqual(get("reply="), "262144")
        self.assertEqual(get("timeout="), "60000")
        self.assertEqual(get("modellen="), "100")
        self.assertEqual(get("localblurb="),
                         "Picked to fit your phone. "
                         "Works offline.")
        self.assertEqual(get("cloudblurb="),
                         "Uses your own API key. Your "
                         "provider's terms and usage limits "
                         "apply.")

    def test_ask_sends_output_cap(self):
        out = self._scene("askok")
        self.assertEqual(self._lines(out, "maxtok="),
                         ["true"])

    def test_truncated_reply_labelled(self):
        out = self._scene("trunc")
        self.assertIn("[truncated: reply exceeded 256 KB]",
                      out)
        self.assertIn("answer|Hi", out)

    def test_retry_shares_one_deadline(self):
        out = self._scene("askretry", "deadline")
        self.assertEqual(
            self._lines(out, "timeouts="),
            ["[60000, 10000]"])
        self.assertEqual(self._lines(out, "net="), ["2"])

    def test_slow_send_times_out(self):
        out = self._scene("slowsend")
        self.assertEqual(
            self._lines(out, "event="),
            ["error|timed out"])
        self.assertEqual(self._lines(out, "net="), ["1"])
        self.assertEqual(self._lines(out, "timeouts="),
                         ["[60000]"])

    def test_late_reply_never_shown(self):
        out = self._scene("slowsend")
        self.assertEqual(
            self._lines(out, "event="),
            ["error|timed out"])
        self.assertNotIn("SECRET-LATE", out)

    def test_trickled_read_stops_at_deadline(self):
        out = self._scene("trickle")
        self.assertEqual(self._lines(out, "threw="),
                         ["true"])
        self.assertEqual(self._lines(out, "closed="),
                         ["true"])
        self.assertEqual(self._lines(out, "readto="),
                         ["20000"])
        self.assertEqual(self._lines(out, "got="), ["10"])
        self.assertEqual(self._lines(out, "fastclosed="),
                         ["true"])
        self.assertEqual(self._lines(out, "fastreadto="),
                         ["50000"])

    def test_retry_wait_past_deadline_times_out(self):
        out = self._scene("latewait")
        self.assertEqual(
            self._lines(out, "event="),
            ["error|timed out"])
        self.assertEqual(self._lines(out, "net="), ["1"])
        self.assertEqual(self._lines(out, "sleeps="),
                         ["[5000]"])
        self.assertEqual(self._lines(out, "timeouts="),
                         ["[60000]"])

    def test_busy_slot_free_after_timeout(self):
        out = self._scene("timeoutthenok")
        self.assertEqual(
            self._lines(out, "first="),
            ["error|timed out"])
        self.assertTrue(
            self._lines(out, "second=")[0].startswith(
                "answer|Hi"), out)
        self.assertEqual(self._lines(out, "net="), ["1"])

    def test_timed_out_transport_cancelled_and_closed(self):
        out = self._scene("cancelclose")
        self.assertEqual(
            self._lines(out, "first="),
            ["error|timed out"])
        self.assertEqual(self._lines(out, "cancelled="),
                         ["1"])
        self.assertEqual(self._lines(out, "running="),
                         ["false"])
        self.assertTrue(
            self._lines(out, "second=")[0].startswith(
                "answer|Hi"), out)

    def test_stalled_read_stops_at_deadline(self):
        # Real loopback server, 2000 ms budget, headers plus
        # one byte at 1900 ms, then a stall. The whole sendTo
        # call including cleanup must throw SocketTimeoutException
        # at the deadline (tolerance -200/+1000 ms: the caller
        # returns via Future.get within ms of the deadline and
        # the abort path never waits for the worker, so +1000 ms
        # covers JVM scheduling/GC jitter while any linger-join
        # or socket overrun fails), the server must see the
        # close, and a second transport must run cleanly once
        # the first worker has exited. The worker stays blocked
        # in its socket read well past the deadline, so the
        # gate must be observably closed (closing1 is 1, next
        # send refused) and the wait for its exit is real.
        out = self._scene("stallread")
        self.assertEqual(self._lines(out, "threw="),
                         ["true|SocketTimeoutException"])
        took = int(self._lines(out, "tookMs=")[0])
        self.assertGreaterEqual(took, 1800)
        self.assertLessEqual(took, 3000)
        self.assertEqual(self._lines(out, "closing1="),
                         ["1"])
        self.assertEqual(self._lines(out, "gate1="),
                         ["rejected|previous request "
                          "still closing"])
        closed = int(self._lines(out, "closedMs=")[0])
        self.assertGreaterEqual(closed, 500)
        self.assertLess(closed, 15000)
        self.assertEqual(self._lines(out, "second="),
                         ["200|true"])
        took2 = int(self._lines(out, "took2Ms=")[0])
        self.assertLess(took2, 3000)
        self.assertEqual(self._lines(out, "sawclose="),
                         ["true"])
        port = int(self._lines(out, "port=")[0])
        self.assertNotIn(port, (8765, 8080))

    def test_stalled_headers_stop_at_deadline(self):
        # Real loopback server, 2000 ms budget, no headers at
        # all, then a stall. The whole sendTo call including
        # cleanup must throw SocketTimeoutException at the
        # deadline (tolerance -200/+1000 ms, as above), the
        # server must see the close, and a second transport must
        # run cleanly once the first worker has exited, proving
        # the timer belonged only to the stalled send.
        out = self._scene("stallheaders")
        self.assertEqual(self._lines(out, "threw="),
                         ["true|SocketTimeoutException"])
        took = int(self._lines(out, "tookMs=")[0])
        self.assertGreaterEqual(took, 1800)
        self.assertLessEqual(took, 3000)
        gate1 = self._lines(out, "gate1=")[0]
        self.assertTrue(
            gate1 == "allowed" or gate1.startswith(
                "rejected|previous request still closing"),
            out)
        self.assertEqual(self._lines(out, "second="),
                         ["200|true"])
        took2 = int(self._lines(out, "took2Ms=")[0])
        self.assertLess(took2, 3000)
        self.assertEqual(self._lines(out, "sawclose="),
                         ["true"])
        port = int(self._lines(out, "port=")[0])
        self.assertNotIn(port, (8765, 8080))

    def test_stalled_write_stops_at_deadline(self):
        # Real loopback server that never reads, 16 MB body,
        # 2000 ms budget. A socket write has no timeout, so
        # only the caller's Future deadline can stop this send
        # (without it the call would run to the server's 4000 ms
        # release). Same type and -200/+1000 ms bound as above;
        # took2 allows 3000 ms because an immediately allowed
        # second send waits for that release timer.
        out = self._scene("stallwrite")
        self.assertEqual(self._lines(out, "threw="),
                         ["true|SocketTimeoutException"])
        took = int(self._lines(out, "tookMs=")[0])
        self.assertGreaterEqual(took, 1800)
        self.assertLessEqual(took, 3000)
        gate1 = self._lines(out, "gate1=")[0]
        self.assertTrue(
            gate1 == "allowed" or gate1.startswith(
                "rejected|previous request still closing"),
            out)
        self.assertEqual(self._lines(out, "second="),
                         ["200|true"])
        took2 = int(self._lines(out, "took2Ms=")[0])
        self.assertLess(took2, 3000)
        port = int(self._lines(out, "port=")[0])
        self.assertNotIn(port, (8765, 8080))

    def test_interrupted_send_closes_admission_until_exited(
            self):
        # Real loopback body stall (headers plus one byte at
        # once, then nothing), 5000 ms budget, caller
        # interrupted at 500 ms. The first call must fail fast
        # (no deadline wait); the worker stays blocked in its
        # socket read, which no close or cancel unblocks, so
        # admission stays closed: closing1 is 1 and the next
        # send is refused until the worker has actually exited,
        # then allowed, and the server sees the close.
        out = self._scene("stallgate")
        self.assertEqual(self._lines(out, "closingMid="),
                         ["0"])
        self.assertEqual(self._lines(out, "alive="),
                         ["false"])
        self.assertEqual(self._lines(out, "threw="),
                         ["true|IOException"])
        took = int(self._lines(out, "tookMs=")[0])
        self.assertLess(took, 2000)
        self.assertEqual(self._lines(out, "closing1="),
                         ["1"])
        self.assertEqual(self._lines(out, "gate1="),
                         ["rejected|previous request "
                          "still closing"])
        closed = int(self._lines(out, "closedMs=")[0])
        self.assertGreaterEqual(closed, 1000)
        self.assertLess(closed, 15000)
        self.assertEqual(self._lines(out, "second="),
                         ["200|true"])
        took2 = int(self._lines(out, "took2Ms=")[0])
        self.assertLess(took2, 3000)
        self.assertEqual(self._lines(out, "sawclose="),
                         ["true"])
        port = int(self._lines(out, "port=")[0])
        self.assertNotIn(port, (8765, 8080))

    def test_previous_request_still_closing_refused(self):
        # Admission mapping: a transport refused while an
        # earlier worker still closes becomes one refusal, not
        # a retry and not "not reachable".
        out = self._scene("stillclosing")
        self.assertEqual(self._lines(out, "event="),
                         ["refused|previous request "
                          "still closing"])
        self.assertEqual(self._lines(out, "net="), ["1"])

    def test_stats_cloud_line(self):
        done = self._java("com.stargate.assistant.StatsLine",
                          "cloud", "Meta", "34", "1200")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(),
                         "Meta · 34 tokens · 1.2 s")
        done = self._java("com.stargate.assistant.StatsLine",
                          "cloud", "Baseten", "-1", "700")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "Baseten · 0.7 s")


class ProviderPinning(unittest.TestCase):
    def test_endpoints(self):
        self.assertEqual(
            m4c.PROVIDERS["meta"]["host"], "api.meta.ai")
        self.assertEqual(
            m4c.PROVIDERS["meta"]["path"],
            "/v1/chat/completions")
        self.assertEqual(
            m4c.PROVIDERS["meta"]["token_field"],
            "max_completion_tokens")
        self.assertEqual(
            m4c.PROVIDERS["baseten"]["host"],
            "inference.baseten.co")
        self.assertEqual(
            m4c.PROVIDERS["baseten"]["path"],
            "/v1/chat/completions")
        self.assertEqual(
            m4c.PROVIDERS["baseten"]["token_field"],
            "max_tokens")

    def test_models(self):
        self.assertEqual(m4c.DEFAULT_META_MODEL,
                         "muse-spark-1.3")
        self.assertEqual(m4c.OPEN_WEIGHT_MODEL,
                         "meta-models/muse-spark-open-1026")

    def test_caps(self):
        self.assertEqual(
            (m4c.QUESTION_MAX_CHARS, m4c.QUESTION_MAX_BYTES,
             m4c.BODY_MAX_BYTES, m4c.OUTPUT_MAX_TOKENS,
             m4c.REPLY_MAX_BYTES, m4c.TIMEOUT_S,
             m4c.RETRY_AFTER_MAX_S, m4c.ATTEMPT_LIMIT,
             m4c.MODEL_ID_MAX_LEN),
            (2000, 8000, 16384, 4096, 262144, 60, 10, 5,
             100))
        self.assertFalse(hasattr(m4c, "SHARE_ATTEMPTS"))
        self.assertFalse(hasattr(m4c, "INPUT_TOKEN_CEILING"))
        self.assertFalse(hasattr(m4c, "Ledger"))
        self.assertFalse(hasattr(m4c, "GateRefused"))

    def test_system_message_matches_app(self):
        from tests.test_assistant import SYSTEM_MESSAGE
        self.assertEqual(m4c.SYSTEM_MESSAGE, SYSTEM_MESSAGE)


class QuestionLimits(unittest.TestCase):
    def test_2000_emoji_accepted(self):
        asked = "\U0001f600" * 2000
        self.assertEqual(m4c.validate_question(asked), asked)
        self.assertEqual(m4c.codepoints(asked), 2000)
        self.assertEqual(m4c.utf8_bytes(asked), 8000)
        req = m4c.build_request("meta", DUMMY_META,
                                "muse-spark-1.3", "SYS",
                                asked)
        self.assertIn(asked, req["body"])

    def test_2001_chars_refused(self):
        with self.assertRaises(m4c.Refused) as held:
            m4c.validate_question("a" * 2001)
        self.assertEqual(held.exception.code, "question")
        with self.assertRaises(m4c.Refused) as held:
            m4c.validate_question("\U0001f600" * 2001)
        self.assertEqual(held.exception.code, "question")

    def test_8001_bytes_refused(self):
        with self.assertRaises(m4c.Refused) as held:
            m4c.validate_question("a" * 8001)
        self.assertEqual(held.exception.code, "question")

    def test_body_shape(self):
        body = m4c.build_body("m", "SYS", "Q?",
                              "max_completion_tokens", 4096)
        parsed = json.loads(body)
        self.assertEqual(parsed["model"], "m")
        self.assertEqual(parsed["max_completion_tokens"], 4096)
        self.assertEqual([item["role"] for item in
                          parsed["messages"]],
                         ["system", "user"])
        self.assertEqual(parsed["messages"][1]["content"],
                         "Q?")

    def test_body_cap_refused(self):
        with self.assertRaises(m4c.Refused) as held:
            m4c.build_request("meta", DUMMY_META,
                              "muse-spark-1.3", "s?" * 9000,
                              "Q?")
        self.assertEqual(held.exception.code, "body")

    def test_output_cap_refused(self):
        with self.assertRaises(m4c.Refused) as held:
            m4c.build_request("meta", DUMMY_META,
                              "muse-spark-1.3", "SYS", "Q?",
                              4097)
        self.assertEqual(held.exception.code, "output")
        req = m4c.build_request("meta", DUMMY_META,
                                "muse-spark-1.3", "SYS",
                                "Q?", 4096)
        self.assertIn('"max_completion_tokens":4096',
                      req["body"])

    def test_model_ids(self):
        self.assertEqual(
            m4c.validate_model("meta", "muse-spark-2.0"),
            "muse-spark-2.0")
        self.assertEqual(
            m4c.validate_model("meta", "a/b.c-d_e"),
            "a/b.c-d_e")
        for bad in ("", "a" * 101, "bad model!",
                    "bad@id", "tab\there"):
            with self.assertRaises(m4c.Refused, msg=repr(bad)):
                m4c.validate_model("meta", bad)
        self.assertEqual(
            m4c.validate_model("baseten", m4c.OPEN_WEIGHT_MODEL),
            m4c.OPEN_WEIGHT_MODEL)
        with self.assertRaises(m4c.Refused):
            m4c.validate_model("baseten", "other-model")


class CredentialRouting(unittest.TestCase):
    def test_each_key_to_its_own_host(self):
        meta = m4c.build_request(
            "meta", DUMMY_META, "muse-spark-1.3", "SYS",
            "Q?")
        self.assertEqual(meta["host"], "api.meta.ai")
        self.assertEqual(meta["auth"], "Bearer " + DUMMY_META)
        self.assertNotIn(DUMMY_BASETEN,
                         json.dumps(meta))
        bw = m4c.build_request(
            "baseten", DUMMY_BASETEN,
            "meta-models/muse-spark-open-1026", "SYS", "Q?")
        self.assertEqual(bw["host"], "inference.baseten.co")
        self.assertEqual(bw["auth"],
                         "Bearer " + DUMMY_BASETEN)
        self.assertNotIn(DUMMY_META, json.dumps(bw))

    def test_bad_model_refused(self):
        with self.assertRaises(m4c.Refused) as held:
            m4c.build_request("meta", DUMMY_META,
                              "bad model!", "SYS", "Q?")
        self.assertEqual(held.exception.code, "model")
        with self.assertRaises(m4c.Refused) as held:
            m4c.build_request("baseten", DUMMY_BASETEN,
                              "other-model", "SYS", "Q?")
        self.assertEqual(held.exception.code, "model")


class ErrorTableMirror(unittest.TestCase):
    def _decide(self, provider, model, status, body,
                retry_after=-1):
        return m4c.decide(provider, model, status, body,
                           m4c.parse_reply(body), retry_after)

    def test_ok_and_incomplete(self):
        body = json.dumps(
            {"choices": [{"message": {"content": "Hi"},
                          "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 10,
                       "completion_tokens": 5}})
        self.assertEqual(
            self._decide("meta", "muse-spark-1.3", 200,
                         body),
            ("answer", "Hi", 0))
        body = json.dumps(
            {"choices": [{"message": {"content": "Hi"},
                          "finish_reason": "length"}]})
        self.assertEqual(
            self._decide("meta", "muse-spark-1.3", 200,
                         body),
            ("answer", "Hi [incomplete]", 0))

    def test_empty_and_malformed(self):
        body = json.dumps(
            {"choices": [{"message": {"content": ""},
                          "finish_reason": "stop"}]})
        self.assertEqual(
            self._decide("meta", "muse-spark-1.3", 200,
                         body),
            ("fail", "No usable answer", 0))
        self.assertEqual(
            self._decide("meta", "muse-spark-1.3", 200,
                         "not json"),
            ("fail", "No usable answer", 0))

    def test_key_payment_model(self):
        self.assertEqual(
            self._decide("meta", "muse-spark-1.3", 401,
                         "SECRET-X"),
            ("fail", "Key rejected by Meta", 0))
        self.assertEqual(
            self._decide("baseten",
                         "meta-models/muse-spark-open-1026",
                         403, "SECRET-X"),
            ("fail", "Key rejected by Baseten", 0))
        self.assertEqual(
            self._decide("meta", "muse-spark-1.3", 402,
                         "SECRET-X"),
            ("fail", "Meta account needs payment", 0))
        self.assertEqual(
            self._decide("meta", "muse-spark-1.3", 400,
                         "billing trouble SECRET-X"),
            ("fail", "Meta account needs payment", 0))
        self.assertEqual(
            self._decide("meta", "muse-spark-1.3", 400,
                         "quota exhausted SECRET-X"),
            ("fail", "Meta account needs payment", 0))
        self.assertEqual(
            self._decide("meta", "muse-spark-1.3", 429,
                         "quota exhausted SECRET-X", 3),
            ("fail", "Meta account needs payment", 0))
        self.assertEqual(
            self._decide("meta", "muse-spark-1.3", 404,
                         "model muse-spark-1.3 gone"),
            ("fail", "Model not available", 0))

    def test_retry_rows(self):
        self.assertEqual(
            self._decide("meta", "muse-spark-1.3", 429,
                         "SECRET-X", 3),
            ("retry", "Too many requests, try later", 3))
        self.assertEqual(
            self._decide("meta", "muse-spark-1.3", 429,
                         "SECRET-X", 30),
            ("fail", "Too many requests, try later", 0))
        self.assertEqual(
            self._decide("meta", "muse-spark-1.3", 429,
                         "SECRET-X"),
            ("retry", "Too many requests, try later", 1))
        self.assertEqual(
            self._decide("meta", "muse-spark-1.3", 500,
                         "SECRET-X"),
            ("retry", "Meta not reachable", 0))

    def test_no_body_shown(self):
        for status, after in ((401, -1), (402, -1),
                              (404, -1), (429, 3),
                              (500, -1)):
            _kind, message, _wait = self._decide(
                "meta", "muse-spark-1.3", status,
                "SECRET-X muse-spark-1.3", after)
            self.assertNotIn("SECRET-X", message, status)


def load_build_py():
    path = REPO / "assistant-app" / "build.py"
    spec = importlib.util.spec_from_file_location(
        "assistant_build", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildVariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = load_build_py()

    def test_permission_parse(self):
        badging = (
            "package: name='com.stargate.assistant'\n"
            "uses-permission: name='android.permission.INTERNET'\n")
        self.assertEqual(
            self.build.permissions_in_badging(badging),
            ["android.permission.INTERNET"])
        self.assertEqual(
            self.build.permissions_in_badging("package: x\n"),
            [])

    def test_local_allows_none(self):
        self.assertEqual(
            self.build.check_permissions("local",
                                         "package: x\n"), [])
        with self.assertRaises(ValueError):
            self.build.check_permissions(
                "local",
                "uses-permission: "
                "name='android.permission.INTERNET'\n")

    def test_cloud_allows_exactly_internet(self):
        badging = ("uses-permission: "
                   "name='android.permission.INTERNET'\n")
        self.assertEqual(
            self.build.check_permissions("cloud", badging),
            ["android.permission.INTERNET"])
        with self.assertRaises(ValueError):
            self.build.check_permissions("cloud",
                                         "package: x\n")
        with self.assertRaises(ValueError):
            self.build.check_permissions(
                "cloud", badging
                + "uses-permission: "
                "name='android.permission.CAMERA'\n")

    def test_gen_config_variants(self):
        tmp = tempfile.mkdtemp(prefix="m4c-gen-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        manifest = REPO / "assistant-manifest.json"
        local_dir = os.path.join(tmp, "local")
        self.build.gen_config(str(manifest), local_dir,
                              "local")
        local_src = Path(local_dir, "com", "stargate",
                         "assistant",
                         "ModelConfig.java").read_text(
                             encoding="utf-8")
        self.assertIn("CLOUD_ENABLED = false", local_src)
        for banned in ("BUILD_ID", "SHARE_ATTEMPTS",
                       "ALLOWED_META", "ALLOWED_BASETEN",
                       "allow", "ledger", "price", "cost",
                       "share"):
            self.assertNotIn(banned, local_src, banned)
        cloud_dir = os.path.join(tmp, "cloud")
        self.build.gen_config(str(manifest), cloud_dir,
                              "cloud")
        cloud_src = Path(cloud_dir, "com", "stargate",
                         "assistant",
                         "ModelConfig.java").read_text(
                             encoding="utf-8")
        self.assertIn("CLOUD_ENABLED = true", cloud_src)
        for banned in ("BUILD_ID", "SHARE_ATTEMPTS",
                       "ALLOWED_META", "ALLOWED_BASETEN",
                       "allow", "ledger", "price", "cost",
                       "share"):
            self.assertNotIn(banned, cloud_src, banned)

    def test_build_has_out_dir_override(self):
        src = (REPO / "assistant-app" / "build.py").read_text(
            encoding="utf-8")
        self.assertIn("--out-dir", src)
        self.assertIn("--build-dir", src)
        self.assertIn("out_dir", src)
        self.assertIn("build_root", src)
        for banned in ("ledger_allowed", "allowed_models",
                       "BUILD_ID", "SHARE_ATTEMPTS",
                       "ALLOWED_"):
            self.assertNotIn(banned, src, banned)


M4_TOOLS = Path(os.environ.get("M4_TOOLS", REPO.parent / "m4-tools"))
AAPT2 = (M4_TOOLS / "android-sdk" / "build-tools" / "34.0.0"
         / "aapt2")


def _load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _badging(apk):
    done = subprocess.run([str(AAPT2), "dump", "badging",
                           str(apk)], capture_output=True,
                          text=True, check=True)
    return done.stdout


def _xmltree(apk, asset):
    done = subprocess.run([str(AAPT2), "dump", "xmltree",
                           str(apk), "--file", asset],
                          capture_output=True, text=True,
                          check=True)
    return done.stdout


@workspace.needs_built_apps
class M4cApk(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not AAPT2.exists():
            raise AssertionError("aapt2 missing under %s"
                                 % M4_TOOLS)
        cls.local_apk = _local_apk()
        cls.cloud_apk = _cloud_apk()
        for apk in (cls.local_apk, cls.cloud_apk):
            if not apk.exists():
                raise AssertionError("missing built APK: %s"
                                     % apk)

    def test_local_has_no_permissions(self):
        badging = _badging(self.local_apk)
        self.assertNotIn("uses-permission", badging)
        with zipfile.ZipFile(self.local_apk) as apk:
            blob = apk.read("AndroidManifest.xml")
        text = blob.replace(b"\x00", b"")
        self.assertEqual(text.count(b"android.permission."), 0)

    def test_cloud_has_exactly_internet(self):
        badging = _badging(self.cloud_apk)
        found = [line for line in badging.splitlines()
                 if "uses-permission" in line]
        self.assertEqual(
            found,
            ["uses-permission: "
             "name='android.permission.INTERNET'"])
        with zipfile.ZipFile(self.cloud_apk) as apk:
            blob = apk.read("AndroidManifest.xml")
        text = blob.replace(b"\x00", b"")
        self.assertIn(b"com.stargate.assistant", text)
        self.assertEqual(text.count(b"android.permission."), 1)
        self.assertIn(b"android.permission.INTERNET", text)

    def test_both_share_package_and_version(self):
        for apk in (self.local_apk, self.cloud_apk):
            badging = _badging(apk)
            self.assertIn(
                "package: name='com.stargate.assistant' "
                "versionCode='1' versionName='1.0'", badging,
                apk.name)

    def test_both_have_no_model_and_licenses(self):
        from tests.test_assistant import model_files_in_apk
        notices = (REPO / "third_party" / "NOTICES.md"
                   ).read_bytes()
        for apk in (self.local_apk, self.cloud_apk):
            with zipfile.ZipFile(apk) as zipped:
                names = zipped.namelist()
                self.assertEqual(model_files_in_apk(names),
                                 [], apk.name)
                self.assertEqual(
                    zipped.read("assets/licenses.txt"),
                    notices, apk.name)
                for abi in ("arm64-v8a", "x86_64"):
                    for lib in ("libexecutorch.so",
                                "libfbjni.so",
                                "libc++_shared.so"):
                        self.assertIn("lib/%s/%s" % (abi, lib),
                                      names, apk.name)

    def test_backup_rules_in_both_apks(self):
        for apk in (self.local_apk, self.cloud_apk):
            manifest = _xmltree(apk, "AndroidManifest.xml")
            self.assertIn("allowBackup", manifest)
            self.assertIn("=false", manifest)
            self.assertIn("dataExtractionRules", manifest)
            rules = _xmltree(
                apk, "res/xml/data_extraction_rules.xml")
            for section in ("cloud-backup",
                            "device-transfer"):
                self.assertIn(section, rules, apk.name)
            for domain in ("root", "file", "database",
                           "sharedpref", "external"):
                self.assertEqual(
                    rules.count('domain="%s"' % domain), 2,
                    (apk.name, domain))

    def test_cloud_apk_has_no_key_strings(self):
        hits = []
        with zipfile.ZipFile(self.cloud_apk) as zipped:
            for name in zipped.namelist():
                blob = zipped.read(name)
                for marker in (b"DUMMY", b"sk-"):
                    if marker in blob:
                        hits.append("%s: %r" % (name, marker))
        self.assertEqual(hits, [])

    def test_manifest_pins_both_apks(self):
        manifest = _load_json(REPO / "assistant-manifest.json")
        for key, apk in (("assistant_apk", self.local_apk),
                         ("assistant_cloud_apk",
                          self.cloud_apk)):
            entry = manifest[key]
            self.assertEqual(entry["package"],
                             "com.stargate.assistant")
            self.assertFalse(os.path.isabs(entry["path"]))
            self.assertEqual(M4B_TOOLS / entry["path"], apk)
            digest = hashlib.sha256()
            with open(apk, "rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20),
                                  b""):
                    digest.update(chunk)
            self.assertEqual(entry["sha256"],
                             digest.hexdigest(), key)


class SourceChecks(unittest.TestCase):
    def test_tls_defaults(self):
        src = (APP_JAVA / "HttpNetworkLayer.java").read_text(
            encoding="utf-8")
        for banned in ("TrustManager", "HostnameVerifier",
                       "SSLSocketFactory",
                       "setDefaultHostnameVerifier",
                       "ALLOW_ALL"):
            self.assertNotIn(banned, src)
        self.assertIn("setInstanceFollowRedirects(false)",
                      src)

    def test_local_path_has_no_network(self):
        src = MAIN_ACTIVITY.read_text(encoding="utf-8")
        start = src.find("private void runLocalQuestion")
        end = src.find("private final class LlmRunner")
        self.assertNotEqual(start, -1)
        self.assertNotEqual(end, -1)
        body = src[start:end]
        for banned in ("CloudAsk", "HttpNetworkLayer",
                       "NetworkLayer", "INTERNET"):
            self.assertNotIn(banned, body)

    def test_cloud_details_are_metadata(self):
        src = MAIN_ACTIVITY.read_text(encoding="utf-8")
        self.assertIn('setDetails("provider="', src)
        start = src.find('setDetails("provider="')
        end = src.find(");", start) + 2
        snippet = src[start:end]
        for banned in ("key", "question", "answer", "Bearer",
                       "auth"):
            self.assertNotIn(banned, snippet.lower(), banned)

    def test_on_screen_text_exact(self):
        spec = (APP_JAVA / "CloudSpec.java").read_text(
            encoding="utf-8")
        self.assertIn("Picked to fit your phone. "
                      "Works offline.", spec)
        self.assertIn("Uses your own API key. Your "
                      "provider's terms and ", spec)
        self.assertIn("usage limits apply.", spec)
        src = MAIN_ACTIVITY.read_text(encoding="utf-8")
        self.assertIn("CloudSpec.LOCAL_BLURB", src)
        self.assertIn("CloudSpec.CLOUD_BLURB", src)
        self.assertIn("CloudSpec.termsUrl(", src)
        self.assertIn("LinkMovementMethod", src)
        self.assertIn("showModeBlurb", src)
        self.assertIn("showCloudBlurb", src)

    def test_terms_urls_fixed(self):
        spec = (APP_JAVA / "CloudSpec.java").read_text(
            encoding="utf-8")
        self.assertIn(
            "https://dev.meta.ai/legal/terms-of-service",
            spec)
        self.assertIn(
            "https://www.baseten.co/terms-and-conditions/",
            spec)
        self.assertNotIn(
            "https://www.meta.ai/legal/terms", spec)
        # The old Baseten URL is a prefix of the new one, so a
        # plain assertNotIn would fail on the new URL: only a
        # whole-string occurrence (followed by a quote or the
        # end) counts.
        self.assertIsNone(
            re.search(
                r"https://www\.baseten\.co/terms(\"|$)",
                spec))
        apk = _need_apk(self, _cloud_apk())
        dex = b""
        with zipfile.ZipFile(apk) as zipped:
            for name in zipped.namelist():
                if name.endswith(".dex"):
                    dex += zipped.read(name)
        self.assertEqual(
            dex.count(
                b"https://dev.meta.ai/legal/terms-of-service"),
            1)
        self.assertEqual(
            dex.count(
                b"https://www.baseten.co/terms-and-conditions/"),
            1)
        self.assertEqual(
            dex.count(b"https://www.meta.ai/legal/terms"), 0)
        stray = [match for match in re.finditer(
            rb"https://www\.baseten\.co/terms", dex)
            if dex[match.end():match.end() + 1] != b"-"]
        self.assertEqual(stray, [])

    def test_no_share_or_allowlist_in_app(self):
        for name in os.listdir(APP_JAVA):
            if not name.endswith(".java"):
                continue
            src = (APP_JAVA / name).read_text(
                encoding="utf-8")
            for banned in ("ShareStore", "PrefsShareStore",
                            "SHARE_ATTEMPTS",
                            "ALLOWED_META", "ALLOWED_BASETEN",
                            "allowed_models", "BUILD_ID"):
                self.assertNotIn(banned, src, (name, banned))
        self.assertFalse((APP_JAVA / "ShareStore.java").exists())
        self.assertFalse(
            (APP_JAVA / "PrefsShareStore.java").exists())

    def test_one_at_a_time_guards(self):
        main = MAIN_ACTIVITY.read_text(encoding="utf-8")
        self.assertIn("busy.compareAndSet(false, true)", main)
        ask = (APP_JAVA / "CloudAsk.java").read_text(
            encoding="utf-8")
        self.assertIn("IN_FLIGHT", ask)
        self.assertIn("Another question is running.", ask)

    def test_key_field_masked_static(self):
        """Static evidence only: the key box masks input.

        The real proof is the Mac run, where the masking is
        checked once with a dummy key on camera, never with a
        real key.
        """
        src = MAIN_ACTIVITY.read_text(encoding="utf-8")
        self.assertIn("import android.text.InputType;", src)
        start = src.find("private void addKeyRow")
        self.assertNotEqual(start, -1)
        end = src.find("private void refreshKeyStatus")
        self.assertNotEqual(end, -1)
        self.assertGreater(end, start)
        body = src[start:end]
        self.assertIn("setInputType(InputType.TYPE_CLASS_TEXT",
                      body)
        self.assertIn("TYPE_TEXT_VARIATION_PASSWORD", body)

    def test_logs_never_hold_secrets(self):
        for name in ("MainActivity.java",
                     "HttpNetworkLayer.java",
                     "CloudAsk.java", "CloudRequest.java"):
            src = (APP_JAVA / name).read_text(
                encoding="utf-8")
            self.assertNotIn("android.util.Log", src, name)
        main = MAIN_ACTIVITY.read_text(encoding="utf-8")
        self.assertNotIn("setDetails(question", main)
        self.assertNotIn("setDetails(answer", main)

    def test_keystore_aes_gcm(self):
        src = (APP_JAVA / "KeystoreKeyStorage.java").read_text(
            encoding="utf-8")
        self.assertIn("AES/GCM/NoPadding", src)
        self.assertIn("AndroidKeyStore", src)

    def test_manifest_backup_rules(self):
        template = (REPO / "assistant-app"
                    / "AndroidManifest.xml").read_text(
                        encoding="utf-8")
        self.assertIn('android:allowBackup="false"',
                      template)
        self.assertIn("android:dataExtractionRules=", template)
        rules = (REPO / "assistant-app" / "res" / "xml"
                 / "data_extraction_rules.xml").read_text(
                     encoding="utf-8")
        for section in ("cloud-backup", "device-transfer"):
            self.assertIn("<" + section + ">", rules)
        for domain in ("root", "file", "database",
                       "sharedpref", "external"):
            self.assertEqual(
                rules.count('<exclude domain="%s" />'
                            % domain),
                2, domain)


class FakePyNet:
    """Scripted fake network layer that counts requests."""

    def __init__(self):
        self.count = 0
        self.seen = []
        self.timeouts = []
        self.script = []
        self.throw_at = set()
        self.ticker = None
        self.advances = []

    def send(self, request, timeout_s=None):
        index = self.count
        self.count += 1
        self.seen.append(request)
        self.timeouts.append(timeout_s)
        if self.ticker is not None:
            if index < len(self.advances):
                self.ticker.now += self.advances[index]
            else:
                self.ticker.now += 50.0
        if index in self.throw_at:
            raise TimeoutError("slow")
        if index < len(self.script):
            return self.script[index]
        return self.script[-1]


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = float(now)

    def __call__(self):
        return self.now


class FakeSleeper:
    def __init__(self, clock=None):
        self.sleeps = []
        self.clock = clock

    def __call__(self, secs):
        self.sleeps.append(secs)
        if self.clock is not None:
            self.clock.now += secs


def _ok_body(text="Hi", prompt=10, completion=5,
             finish="stop"):
    return json.dumps(
        {"choices": [{"message": {"content": text},
                      "finish_reason": finish}],
         "usage": {"prompt_tokens": prompt,
                   "completion_tokens": completion}})


class CallsLogGate(unittest.TestCase):
    def _log(self):
        tmp = tempfile.mkdtemp(prefix="m4c-calls-")
        self.addCleanup(shutil.rmtree, tmp,
                        ignore_errors=True)
        return m4c.CallsLog(os.path.join(tmp, "calls.md"))

    def _attempt_args(self, question="Q?"):
        return {"provider": "meta",
                "model": "muse-spark-1.3",
                "system": "SYS", "question": question,
                "key": DUMMY_META}

    def test_real_calls_md_has_no_money(self):
        text = (REPO / "docs" / "m4c-calls.md").read_text(
            encoding="utf-8")
        for banned in ("$", "usd", "USD", "price", "Price",
                        "PRICE", "cost", "Cost", "dollar",
                        "Dollar", "UNPRICED", "estimate",
                        "ceiling", "ledger", "Ledger",
                        "reserve", "share", "Share"):
            self.assertNotIn(banned, text, banned)
        self.assertFalse(
            (REPO / "docs" / "m4c-ledger.json").exists())
        log = m4c.CallsLog(REPO / "docs" / "m4c-calls.md")
        self.assertEqual(log.attempts_used("meta"), 0)
        self.assertEqual(log.attempts_used("baseten"), 0)

    def test_sixth_send_refused(self):
        log = self._log()
        net = FakePyNet()
        net.script.append({"status": 200,
                           "body": _ok_body()})
        for _ in range(5):
            log.attempt(network=net,
                        **self._attempt_args())
        self.assertEqual(net.count, 5)
        with self.assertRaises(m4c.Refused) as held:
            log.attempt(network=net,
                        **self._attempt_args())
        self.assertEqual(held.exception.code, "limit")
        self.assertEqual(net.count, 5)
        self.assertEqual(log.attempts_used("meta"), 5)

    def test_retry_counts(self):
        log = self._log()
        net = FakePyNet()
        net.script.append({"status": 200,
                           "body": _ok_body()})
        log.attempt(network=net, **self._attempt_args())
        log.attempt(network=net, **self._attempt_args())
        self.assertEqual(log.attempts_used("meta"), 2)
        self.assertEqual(
            [row["state"] for row in log.rows],
            ["sent", "sent"])

    def test_planned_written_before_send_and_survives(self):
        tmp = tempfile.mkdtemp(prefix="m4c-restart-")
        self.addCleanup(shutil.rmtree, tmp,
                        ignore_errors=True)
        path = os.path.join(tmp, "calls.md")
        first = m4c.CallsLog(path)
        row = first.plan("meta", "muse-spark-1.3",
                         "codespace",
                         at="2026-11-02T12:00:00Z")
        self.assertEqual(row["state"], "planned")
        text = Path(path).read_text(encoding="utf-8")
        self.assertIn("| planned |", text)
        self.assertIn("2026-11-02T12:00:00Z", text)
        second = m4c.CallsLog(path)
        self.assertEqual(second.attempts_used("meta"), 1)
        self.assertEqual(second.rows[0]["state"], "planned")

    def test_timeout_leaves_uncertain_that_counts(self):
        log = self._log()
        net = FakePyNet()
        net.throw_at.add(0)
        with self.assertRaises(TimeoutError):
            log.attempt(network=net,
                        **self._attempt_args())
        self.assertEqual(log.attempts_used("meta"), 1)
        self.assertEqual(log.rows[0]["state"], "uncertain")
        again = m4c.CallsLog(log.path)
        self.assertEqual(again.attempts_used("meta"), 1)
        self.assertEqual(again.rows[0]["state"], "uncertain")
        net2 = FakePyNet()
        net2.script.append({"status": 200,
                            "body": _ok_body()})
        for _ in range(4):
            log.attempt(network=net2,
                        **self._attempt_args())
        self.assertEqual(log.attempts_used("meta"), 5)
        with self.assertRaises(m4c.Refused):
            log.attempt(network=net2,
                        **self._attempt_args())

    def test_internal_typeerror_sends_once_and_propagates(self):
        log = self._log()

        class BadSend:
            def __init__(self):
                self.count = 0

            def send(self, request, timeout_s=None):
                self.count += 1
                raise TypeError("boom inside send")

        net = BadSend()
        with self.assertRaises(TypeError):
            log.attempt(network=net,
                        **self._attempt_args())
        self.assertEqual(net.count, 1)
        self.assertEqual(log.attempts_used("meta"), 1)
        self.assertEqual(log.rows[0]["state"], "uncertain")

    def test_sent_holds_status_and_usage(self):
        log = self._log()
        net = FakePyNet()
        net.script.append({"status": 200,
                           "body": _ok_body()})
        done = log.attempt(network=net,
                           **self._attempt_args())
        self.assertEqual(done["row"]["state"], "sent")
        self.assertEqual(done["row"]["status"], "200")
        self.assertEqual(done["row"]["prompt"], "10")
        self.assertEqual(done["row"]["completion"], "5")
        self.assertFalse(done["truncated"])

    def test_missing_usage_still_sent(self):
        log = self._log()
        net = FakePyNet()
        net.script.append({"status": 200, "body": "{}"})
        done = log.attempt(network=net,
                           **self._attempt_args())
        self.assertEqual(done["row"]["state"], "sent")
        self.assertEqual(done["row"]["prompt"], "")

    def test_preflight_refuses_with_four_used(self):
        log = self._log()
        net = FakePyNet()
        net.script.append({"status": 200,
                           "body": _ok_body()})
        for _ in range(4):
            log.attempt(network=net,
                        **self._attempt_args())
        with self.assertRaises(m4c.Refused) as held:
            log.preflight_phone("meta", "muse-spark-1.3")
        self.assertEqual(held.exception.code, "preflight")
        self.assertEqual(log.attempts_used("meta"), 4)
        self.assertEqual(net.count, 4)

    def test_preflight_writes_two_phone_rows(self):
        log = self._log()
        rows = log.preflight_phone(
            "meta", "muse-spark-1.3",
            at="2026-11-02T12:00:00Z")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(item["where"] == "phone"
                            for item in rows))
        self.assertTrue(all(item["state"] == "planned"
                            for item in rows))
        self.assertEqual(log.attempts_used("meta"), 2)

    def test_reconcile_sent_uncertain_delete(self):
        log = self._log()
        planned = log.plan("meta", "muse-spark-1.3",
                           "phone")
        sent = log.mark_sent(planned["id"], 200, 10, 5)
        self.assertEqual(sent["state"], "sent")
        second = log.plan("meta", "muse-spark-1.3",
                          "phone")
        uncertain = log.mark_uncertain(second["id"])
        self.assertEqual(uncertain["state"], "uncertain")
        with self.assertRaises(m4c.Refused):
            log.delete_planned(second["id"])
        third = log.plan("baseten",
                         "meta-models/muse-spark-open-1026",
                         "phone")
        log.delete_planned(third["id"])
        self.assertEqual(log.attempts_used("baseten"), 0)
        self.assertEqual(log.attempts_used("meta"), 2)

    def test_log_never_holds_secrets_or_money(self):
        log = self._log()
        net = FakePyNet()
        net.script.append({"status": 200,
                           "body": _ok_body(
                               text="SECRET-ANSWER-99")})
        args = self._attempt_args(
            question="SECRET-QUESTION-99")
        args["key"] = DUMMY_META
        log.attempt(network=net, **args)
        text = Path(log.path).read_text(encoding="utf-8")
        for banned in (DUMMY_META, "SECRET-QUESTION-99",
                        "SECRET-ANSWER-99", "$", "usd",
                        "USD", "price", "cost", "dollar",
                        "estimate", "ceiling", "reserve",
                        "share"):
            self.assertNotIn(banned, text, banned)

    def test_2000_emoji_sent_intact(self):
        question = "\U0001f600" * 2000
        log = self._log()
        net = FakePyNet()
        net.script.append({"status": 200,
                           "body": _ok_body()})
        log.attempt(network=net,
                    **self._attempt_args(question))
        self.assertEqual(net.count, 1)
        self.assertIn(question, net.seen[0]["body"])

    def test_body_over_16k_refused_no_row(self):
        log = self._log()
        net = FakePyNet()
        net.script.append({"status": 200,
                           "body": _ok_body()})
        args = self._attempt_args()
        args["system"] = "s?" * 9000
        with self.assertRaises(m4c.Refused) as held:
            log.attempt(network=net, **args)
        self.assertEqual(held.exception.code, "body")
        self.assertEqual(net.count, 0)
        self.assertEqual(log.attempts_used("meta"), 0)

    def test_question_limits_refused_no_row(self):
        log = self._log()
        net = FakePyNet()
        net.script.append({"status": 200,
                           "body": _ok_body()})
        for bad in ("a" * 2001,
                    "\U0001f600" * 2001, "a" * 8001):
            with self.assertRaises(m4c.Refused, msg=bad[:8]):
                log.attempt(
                    network=net,
                    **self._attempt_args(bad))
        self.assertEqual(net.count, 0)
        self.assertEqual(log.attempts_used("meta"), 0)


class AskRetryGate(unittest.TestCase):
    def _log(self):
        tmp = tempfile.mkdtemp(prefix="m4c-ask-")
        self.addCleanup(shutil.rmtree, tmp,
                        ignore_errors=True)
        return m4c.CallsLog(os.path.join(tmp, "calls.md"))

    def _run(self, script, clock=None, sleeper=None,
             question="Q?", throw_at=()):
        log = self._log()
        net = FakePyNet()
        net.throw_at.update(throw_at)
        net.script.extend(script)
        if clock is None:
            clock = FakeClock()
        if sleeper is None:
            sleeper = FakeSleeper()
        done = m4c.ask(
            "meta", "muse-spark-1.3", "SYS", question,
            DUMMY_META, net, log, clock=clock,
            sleeper=sleeper)
        return done, log, net, clock, sleeper

    def test_402_never_retried(self):
        done, log, net, _c, sleeper = self._run([
            {"status": 402, "body": '{"error":"pay"}'},
            {"status": 200, "body": _ok_body()}])
        self.assertEqual(done["kind"], "fail")
        self.assertEqual(done["message"],
                         "Meta account needs payment")
        self.assertEqual(net.count, 1)
        self.assertEqual(sleeper.sleeps, [])
        self.assertEqual(log.attempts_used("meta"), 1)

    def test_quota_never_retried(self):
        for body in ('{"error":"quota exhausted"}',
                      '{"error":"Quota exceeded"}'):
            done, log, net, _c, sleeper = self._run([
                {"status": 429, "body": body,
                 "retry_after": 3},
                {"status": 200, "body": _ok_body()}])
            self.assertEqual(done["kind"], "fail", body)
            self.assertEqual(net.count, 1, body)
            self.assertEqual(sleeper.sleeps, [], body)

    def test_429_short_retried_once(self):
        done, log, net, _c, sleeper = self._run([
            {"status": 429, "body": '{"error":"slow"}',
             "retry_after": 3},
            {"status": 200, "body": _ok_body()}])
        self.assertEqual(done["kind"], "answer")
        self.assertEqual(net.count, 2)
        self.assertEqual(sleeper.sleeps, [3])
        self.assertEqual(log.attempts_used("meta"), 2)
        self.assertTrue(all(
            item["host"] == "api.meta.ai"
            for item in net.seen))
        self.assertTrue(all(
            item["model"] == "muse-spark-1.3"
            for item in net.seen))

    def test_429_long_not_retried(self):
        done, _log, net, _c, sleeper = self._run([
            {"status": 429, "body": '{"error":"slow"}',
             "retry_after": 30}])
        self.assertEqual(done["kind"], "fail")
        self.assertEqual(net.count, 1)
        self.assertEqual(sleeper.sleeps, [])

    def test_second_retry_never(self):
        done, _log, net, _c, _s = self._run([
            {"status": 500, "body": '{"error":"down"}'},
            {"status": 500, "body": '{"error":"down"}'},
            {"status": 200, "body": _ok_body()}])
        self.assertEqual(done["kind"], "fail")
        self.assertEqual(net.count, 2)

    def test_timeout_retried_once(self):
        done, _log, net, _c, _s = self._run(
            [{"status": 200, "body": _ok_body()}],
            throw_at=(0,))
        self.assertEqual(done["kind"], "answer")
        self.assertEqual(net.count, 2)

    def test_wait_included_in_deadline(self):
        clock = FakeClock()
        sleeper = FakeSleeper(clock)
        done, _log, net, _c, _s = self._run(
            [{"status": 429, "body": '{"error":"slow"}',
              "retry_after": 3},
             {"status": 200, "body": _ok_body()}],
            clock=clock, sleeper=sleeper)
        self.assertEqual(done["kind"], "answer")
        self.assertEqual(net.count, 2)
        self.assertEqual(sleeper.sleeps, [3])
        self.assertAlmostEqual(net.timeouts[0], 60.0)
        self.assertAlmostEqual(net.timeouts[1], 57.0)

    def test_deadline_with_ticker(self):
        log = self._log()
        net = FakePyNet()
        clock = FakeClock()
        net.ticker = clock
        net.script.extend([
            {"status": 500, "body": '{"error":"down"}'},
            {"status": 200, "body": _ok_body()}])
        done = m4c.ask(
            "meta", "muse-spark-1.3", "SYS", "Q?",
            DUMMY_META, net, log, clock=clock,
            sleeper=FakeSleeper())
        # Both sends take 50 s each, so the retry's reply
        # arrives after the 60 s deadline and is dropped.
        self.assertEqual(done["kind"], "fail")
        self.assertEqual(done["message"], "timed out")
        self.assertEqual(net.count, 2)
        self.assertAlmostEqual(net.timeouts[0], 60.0)
        self.assertAlmostEqual(net.timeouts[1], 10.0)

    def test_slow_connect_then_read_times_out(self):
        log = self._log()
        net = FakePyNet()
        clock = FakeClock()
        net.ticker = clock
        net.advances = [70.0]
        net.script.append({"status": 200,
                           "body": _ok_body()})
        done = m4c.ask(
            "meta", "muse-spark-1.3", "SYS", "Q?",
            DUMMY_META, net, log, clock=clock,
            sleeper=FakeSleeper())
        self.assertEqual(done["kind"], "fail")
        self.assertEqual(done["message"], "timed out")
        self.assertEqual(net.count, 1)
        self.assertEqual(log.attempts_used("meta"), 1)

    def test_trickled_reply_aborted_at_deadline(self):
        log = self._log()
        net = FakePyNet()
        clock = FakeClock()
        net.ticker = clock
        net.advances = [70.0]
        net.throw_at.add(0)
        done = m4c.ask(
            "meta", "muse-spark-1.3", "SYS", "Q?",
            DUMMY_META, net, log, clock=clock,
            sleeper=FakeSleeper())
        self.assertEqual(done["kind"], "fail")
        self.assertEqual(done["message"], "timed out")
        self.assertEqual(net.count, 1)
        self.assertEqual(log.rows[0]["state"], "uncertain")

    def test_retry_wait_past_deadline_times_out(self):
        log = self._log()
        net = FakePyNet()
        clock = FakeClock()
        net.ticker = clock
        net.advances = [55.0]
        net.script.extend([
            {"status": 429, "body": '{"error":"slow"}',
             "retry_after": 10},
            {"status": 200, "body": _ok_body()}])
        sleeper = FakeSleeper(clock)
        done = m4c.ask(
            "meta", "muse-spark-1.3", "SYS", "Q?",
            DUMMY_META, net, log, clock=clock,
            sleeper=sleeper)
        self.assertEqual(done["kind"], "fail")
        self.assertEqual(done["message"], "timed out")
        self.assertEqual(net.count, 1)
        self.assertEqual(sleeper.sleeps, [5.0])

    def test_busy_slot_free_after_timeout(self):
        log = self._log()
        slow = FakePyNet()
        clock = FakeClock()
        slow.ticker = clock
        slow.advances = [70.0]
        slow.script.append({"status": 200,
                            "body": _ok_body()})
        first = m4c.ask(
            "meta", "muse-spark-1.3", "SYS", "Q?",
            DUMMY_META, slow, log, clock=clock,
            sleeper=FakeSleeper())
        self.assertEqual(first["message"], "timed out")
        fast = FakePyNet()
        fast.script.append({"status": 200,
                            "body": _ok_body()})
        second = m4c.ask(
            "meta", "muse-spark-1.3", "SYS", "Q2?",
            DUMMY_META, fast, log, clock=FakeClock(),
            sleeper=FakeSleeper())
        self.assertEqual(second["kind"], "answer")
        self.assertEqual(fast.count, 1)

    def test_late_reply_never_shown(self):
        log = self._log()
        net = FakePyNet()
        clock = FakeClock()
        net.ticker = clock
        net.advances = [70.0]
        net.script.append(
            {"status": 200,
             "body": _ok_body(text="SECRET-LATE-ANSWER")})
        done = m4c.ask(
            "meta", "muse-spark-1.3", "SYS", "Q?",
            DUMMY_META, net, log, clock=clock,
            sleeper=FakeSleeper())
        self.assertEqual(done["message"], "timed out")
        self.assertNotIn("SECRET-LATE-ANSWER",
                         json.dumps(done))
        self.assertEqual(net.count, 1)

    def test_timed_out_transport_cancelled_and_closed(self):
        log = self._log()

        class StuckNet:
            def __init__(self, clock):
                self.clock = clock
                self.count = 0
                self.running = False
                self.cancel_calls = 0

            def send(self, request, timeout_s=None):
                self.count += 1
                self.running = True
                self.clock.now += 70.0
                raise TimeoutError("stuck mid-read")

            def cancel(self):
                self.cancel_calls += 1
                self.running = False

        clock = FakeClock()
        net = StuckNet(clock)
        done = m4c.ask(
            "meta", "muse-spark-1.3", "SYS", "Q?",
            DUMMY_META, net, log, clock=clock,
            sleeper=FakeSleeper())
        self.assertEqual(done["message"], "timed out")
        self.assertEqual(net.cancel_calls, 1)
        self.assertFalse(net.running)
        fast = FakePyNet()
        fast.script.append({"status": 200,
                            "body": _ok_body()})
        second = m4c.ask(
            "meta", "muse-spark-1.3", "SYS", "Q2?",
            DUMMY_META, fast, log, clock=FakeClock(),
            sleeper=FakeSleeper())
        self.assertEqual(second["kind"], "answer")

    def test_busy_refused_before_network(self):
        log = self._log()
        entered = threading.Event()
        release = threading.Event()
        counts = []

        class Blocking:
            def send(self, request, timeout_s=None):
                counts.append(request)
                if len(counts) == 1:
                    entered.set()
                    release.wait(timeout=10)
                return {"status": 200,
                        "body": _ok_body()}

        net = Blocking()
        first = {}
        worker = threading.Thread(
            target=lambda: first.update(
                m4c.ask("meta", "muse-spark-1.3", "SYS",
                        "Q?", DUMMY_META, net, log,
                        clock=FakeClock(),
                        sleeper=FakeSleeper())))
        worker.start()
        self.assertTrue(entered.wait(timeout=10))
        with self.assertRaises(m4c.Refused) as held:
            m4c.ask("meta", "muse-spark-1.3", "SYS", "Q2?",
                    DUMMY_META, net, log,
                    clock=FakeClock(),
                    sleeper=FakeSleeper())
        self.assertEqual(held.exception.code, "busy")
        self.assertEqual(len(counts), 1)
        release.set()
        worker.join(timeout=10)
        self.assertEqual(first.get("kind"), "answer")
        self.assertEqual(len(counts), 1)

    def test_truncation_labelled(self):
        big = "Hi" + "x" * 300000
        body = _ok_body(text=big)
        self.assertGreater(m4c.utf8_bytes(body),
                           m4c.REPLY_MAX_BYTES)
        done, _log, net, _c, _s = self._run([
            {"status": 200, "body": body}])
        self.assertEqual(done["kind"], "answer")
        self.assertIn("[truncated: reply exceeded 256 KB]",
                      done["message"])
        self.assertEqual(net.count, 1)


class RemovalCheck(unittest.TestCase):
    """Section 7 removal check, scoped as the spec says.

    Exceptions (allowed): documentation that describes the
    removal, and negative tests that check it (this file and
    the removal sections of docs/m4c-integration.md).
    """

    OLD_NAMES = [
        "input_usd_per_1k", "output_usd_per_1k",
        "price_source", "input_token_ceiling",
        "ceiling_source", "INPUT_TOKEN_CEILING",
        "dollar_limits", "dollar_cap", "committed_usd",
        "estimate_usd", "cost_usd", "actual_spend",
        "owner_credit", "most_expensive_priced",
        "allowed_models_for_provider",
        "ALLOWED_META_MODELS",
        "ALLOWED_BASETEN_MODELS",
        "SHARE_ATTEMPTS", "ShareStore",
        "PrefsShareStore", "reserve_build_share",
        "reserve_cloud_share", "build share",
        "build_share", "share is used",
        "m4c-share", "GateRefused",
        "render_calls_md", "UNPRICED",
        "attempt_limit_per_provider",
        "usage above reservation",
        "would pass the ledger limit",
        "provider halted",
        "model has no pinned price",
        "no priced model",
    ]

    EXCEPTIONS = [
        "tests/test_m4c.py",
        "docs/m4c-integration.md",
    ]

    def _scan_text(self, text, where):
        hits = []
        for name in self.OLD_NAMES:
            if name in text:
                hits.append("%s: %s" % (where, name))
        return hits

    def test_no_money_or_share_in_code(self):
        hits = []
        for path in [REPO / "stargate" / "m4c.py",
                     REPO / "assistant-app" / "build.py"]:
            text = path.read_text(encoding="utf-8")
            hits.extend(self._scan_text(text, path.name))
        for name in os.listdir(APP_JAVA):
            if not name.endswith(".java"):
                continue
            text = (APP_JAVA / name).read_text(
                encoding="utf-8")
            hits.extend(self._scan_text(text, name))
        self.assertEqual(hits, [])

    def test_no_priced_ledger_class(self):
        self.assertFalse(hasattr(m4c, "Ledger"))
        self.assertFalse(hasattr(m4c, "priced"))
        self.assertFalse(
            hasattr(m4c, "allowed_models_for_provider"))
        self.assertTrue(hasattr(m4c, "CallsLog"))

    def test_calls_log_has_no_money(self):
        text = (REPO / "docs" / "m4c-calls.md").read_text(
            encoding="utf-8")
        hits = self._scan_text(text, "m4c-calls.md")
        self.assertEqual(hits, [])
        for extra in ("$", "usd", "USD", "price", "cost",
                       "dollar", "estimate", "ceiling",
                       "reserve", "share"):
            self.assertNotIn(extra, text, extra)

    def test_cloud_apk_has_no_money_names(self):
        apk = _need_apk(self, _cloud_apk())
        hits = []
        with zipfile.ZipFile(apk) as zipped:
            for name in zipped.namelist():
                blob = zipped.read(name)
                try:
                    text = blob.decode("utf-8",
                                       errors="ignore")
                except Exception:
                    continue
                for old in self.OLD_NAMES:
                    if old in text:
                        hits.append("%s: %s" % (name, old))
        self.assertEqual(hits, [])


class MacProofGuard(unittest.TestCase):
    GUARD_FENCE = "```sh\n# check-device guard"

    def _bodies(self):
        text = (REPO / "docs" / "m4c-mac-proof.md"
                ).read_text(encoding="utf-8")
        self.assertEqual(text.count(self.GUARD_FENCE), 1)
        bodies = []
        pos = 0
        while True:
            start = text.find("```sh\n", pos)
            if start == -1:
                break
            end = text.find("```", start + 6)
            self.assertNotEqual(end, -1)
            body = text[start + 6:end]
            pos = end + 3
            if body.startswith("# check-device guard"):
                continue
            bodies.append(body)
        self.assertTrue(bodies)
        return bodies

    def test_every_adb_line_guarded(self):
        for body in self._bodies():
            for line in body.splitlines():
                if "adb" not in line:
                    continue
                if line.strip() == "adb devices":
                    continue
                self.assertIn("guard", line, line)

    def test_avd_and_live_marks(self):
        text = (REPO / "docs" / "m4c-mac-proof.md"
                ).read_text(encoding="utf-8")
        self.assertIn("stargate-m4c-cloud", text)
        flat = " ".join(text.lower().split())
        self.assertEqual(flat.count("owner's ok"), 3)
        for banned in ("filled-in prices", "ledger",
                        "billing", "spend", "dollar",
                        "price", "UNPRICED", "reserve"):
            self.assertNotIn(banned, flat, banned)

    def test_preflight_and_reconcile_present(self):
        text = (REPO / "docs" / "m4c-mac-proof.md"
                ).read_text(encoding="utf-8")
        cloud = _manifest()["assistant_cloud_apk"]
        for need in ("Preflight before any live question",
                      "preflight_phone", "attempts_used",
                      "Reconcile after the run",
                      "mark_sent", "mark_uncertain",
                      "delete_planned",
                      "No more live work starts until",
                      cloud["path"],
                      cloud["sha256"]):
            self.assertIn(need, text, need)

    def test_blocks_are_one_command_per_line(self):
        for body in self._bodies():
            for line in body.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                self.assertNotIn("<", line, line)
                self.assertNotIn(">", line, line)


if __name__ == "__main__":
    unittest.main()
