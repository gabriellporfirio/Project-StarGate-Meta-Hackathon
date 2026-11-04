"""Offline tests for stargate doctor: no network, no real credentials."""
from __future__ import annotations

import io
import json
import os
import socket
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from stargate import cli, doctor

FAKE_KEY = "sk-test-fake-key-0000"
LISTED_MODEL = "muse-spark-1.2"


def _ok_payload(model=LISTED_MODEL, content="ok"):
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }


def _error_payload(message):
    return {"error": {"message": message, "type": "test", "code": "test"}}


def _raw(payload):
    if isinstance(payload, bytes):
        return payload
    return json.dumps(payload).encode("utf-8")


def fixed_send(status, payload):
    def send(url, body, headers, timeout):
        send.calls.append(
            {"url": url, "body": body, "headers": dict(headers), "timeout": timeout}
        )
        return status, _raw(payload)

    send.calls = []
    return send


def sequence_send(steps):
    """Each step is a (status, payload) tuple or an exception to raise."""

    def send(url, body, headers, timeout):
        send.calls.append(
            {"url": url, "body": body, "headers": dict(headers), "timeout": timeout}
        )
        step = steps[min(len(send.calls) - 1, len(steps) - 1)]
        if isinstance(step, BaseException):
            raise step
        status, payload = step
        return status, _raw(payload)

    send.calls = []
    return send


def no_sleep(seconds):
    pass


PINNED_PLAN = {
    "path": "/tmp/test-plan",
    "head": doctor.PLAN_COMMIT,
    "clean": True,
    "error": None,
}


def _pinned_plan():
    return mock.patch.object(doctor, "gather_plan_pin", return_value=dict(PINNED_PLAN))


class InferenceResultCodes(unittest.TestCase):
    def test_ok(self):
        send = fixed_send(200, _ok_payload())
        res = doctor.run_inference(FAKE_KEY, send=send, sleep=no_sleep)
        self.assertEqual(res["code"], doctor.OK)
        self.assertEqual(doctor.EXIT_CODES[res["code"]], 0)
        self.assertEqual(res["endpoint"], "https://api.meta.ai/v1/chat/completions")
        self.assertEqual(res["model_requested"], doctor.META_MODEL)
        self.assertEqual(res["model_returned"], LISTED_MODEL)
        self.assertEqual(res["attempts"], 1)

    def test_ok_without_returned_model(self):
        payload = _ok_payload()
        del payload["model"]
        send = fixed_send(200, payload)
        res = doctor.run_inference(FAKE_KEY, send=send, sleep=no_sleep)
        self.assertEqual(res["code"], doctor.OK)
        self.assertIsNone(res["model_returned"])

    def test_ok_with_content_blocks(self):
        payload = _ok_payload(content=[{"type": "text", "text": "ok"}])
        send = fixed_send(200, payload)
        res = doctor.run_inference(FAKE_KEY, send=send, sleep=no_sleep)
        self.assertEqual(res["code"], doctor.OK)

    def test_auth_failed(self):
        for status in (401, 403):
            with self.subTest(status=status):
                send = fixed_send(status, _error_payload("bad key"))
                res = doctor.run_inference(FAKE_KEY, send=send, sleep=no_sleep)
                self.assertEqual(res["code"], doctor.AUTH_FAILED)
                self.assertEqual(doctor.EXIT_CODES[res["code"]], 3)
                self.assertEqual(res["attempts"], 1)  # no retry

    def test_quota_or_rate_limited(self):
        for status in (429, 402):
            with self.subTest(status=status):
                send = sequence_send(
                    [
                        (status, _error_payload("slow down")),
                        (status, _error_payload("slow down")),
                        (200, _ok_payload()),
                    ]
                )
                res = doctor.run_inference(FAKE_KEY, send=send, sleep=no_sleep)
                self.assertEqual(res["code"], doctor.QUOTA_OR_RATE_LIMITED)
                self.assertEqual(doctor.EXIT_CODES[res["code"]], 4)
                self.assertEqual(len(send.calls), 2)  # exactly 1 retry

    def test_quota_recovers_on_retry(self):
        send = sequence_send(
            [(429, _error_payload("slow down")), (200, _ok_payload())]
        )
        res = doctor.run_inference(FAKE_KEY, send=send, sleep=no_sleep)
        self.assertEqual(res["code"], doctor.OK)
        self.assertEqual(res["attempts"], 2)

    def test_network_error(self):
        send = sequence_send(
            [doctor.NetworkFailure("boom"), doctor.NetworkFailure("boom")]
        )
        res = doctor.run_inference(FAKE_KEY, send=send, sleep=no_sleep)
        self.assertEqual(res["code"], doctor.NETWORK_ERROR)
        self.assertEqual(doctor.EXIT_CODES[res["code"]], 5)
        self.assertEqual(len(send.calls), 2)

    def test_network_error_recovers_on_retry(self):
        send = sequence_send([doctor.NetworkFailure("boom"), (200, _ok_payload())])
        res = doctor.run_inference(FAKE_KEY, send=send, sleep=no_sleep)
        self.assertEqual(res["code"], doctor.OK)
        self.assertEqual(res["attempts"], 2)

    def test_raw_transport_exception_maps_to_network_error(self):
        send = sequence_send([ValueError("bad transport"), (200, _ok_payload())])
        res = doctor.run_inference(FAKE_KEY, send=send, sleep=no_sleep)
        self.assertEqual(res["code"], doctor.OK)  # retried, then fine
        send2 = sequence_send([ValueError("x"), ValueError("x")])
        res2 = doctor.run_inference(FAKE_KEY, send=send2, sleep=no_sleep)
        self.assertEqual(res2["code"], doctor.NETWORK_ERROR)

    def test_bad_response_shapes(self):
        cases = {
            "invalid-json": b"not json{",
            "non-dict": [1, 2, 3],
            "no-choices": {"model": "m"},
            "empty-choices": {"choices": []},
            "empty-content": _ok_payload(content=""),
            "blank-content": _ok_payload(content="   "),
        }
        for name, payload in cases.items():
            with self.subTest(name=name):
                send = fixed_send(200, payload)
                res = doctor.run_inference(FAKE_KEY, send=send, sleep=no_sleep)
                self.assertEqual(res["code"], doctor.BAD_RESPONSE)
                self.assertEqual(doctor.EXIT_CODES[res["code"]], 6)

    def test_bad_request_status(self):
        send = fixed_send(400, _error_payload("bad request"))
        res = doctor.run_inference(FAKE_KEY, send=send, sleep=no_sleep)
        self.assertEqual(res["code"], doctor.BAD_RESPONSE)

    def test_bad_response_null_content_when_truncated(self):
        # Observed live: reasoning eats the token budget, finish_reason=length,
        # content=null. Strictly a bad response (no valid non-empty answer).
        payload = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "model": LISTED_MODEL,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "length",
                    "message": {"role": "assistant", "content": None},
                }
            ],
            "usage": {"completion_tokens": 64},
        }
        send = fixed_send(200, payload)
        res = doctor.run_inference(FAKE_KEY, send=send, sleep=no_sleep)
        self.assertEqual(res["code"], doctor.BAD_RESPONSE)
        self.assertEqual(doctor.EXIT_CODES[res["code"]], 6)
        self.assertIn("length", res["detail"])
        call = send.calls[0]
        body = json.loads(call["body"].decode("utf-8"))
        self.assertEqual(body["max_completion_tokens"], 1024)

    def test_model_not_available(self):
        send = fixed_send(404, _error_payload("no such model"))
        res = doctor.run_inference(FAKE_KEY, send=send, sleep=no_sleep)
        self.assertEqual(res["code"], doctor.MODEL_NOT_AVAILABLE)
        self.assertEqual(doctor.EXIT_CODES[res["code"]], 7)
        self.assertEqual(res["attempts"], 1)  # no retry

    def test_server_error_retried_then_network_error(self):
        send = sequence_send(
            [(500, _error_payload("oops")), (503, _error_payload("oops"))]
        )
        res = doctor.run_inference(FAKE_KEY, send=send, sleep=no_sleep)
        self.assertEqual(res["code"], doctor.NETWORK_ERROR)
        self.assertEqual(len(send.calls), 2)

    def test_server_error_recovers_on_retry(self):
        send = sequence_send([(500, _error_payload("oops")), (200, _ok_payload())])
        res = doctor.run_inference(FAKE_KEY, send=send, sleep=no_sleep)
        self.assertEqual(res["code"], doctor.OK)

    def test_no_retry_after_auth_failure(self):
        send = sequence_send([(401, _error_payload("bad key")), (200, _ok_payload())])
        res = doctor.run_inference(FAKE_KEY, send=send, sleep=no_sleep)
        self.assertEqual(res["code"], doctor.AUTH_FAILED)
        self.assertEqual(len(send.calls), 1)

    def test_request_shape(self):
        send = fixed_send(200, _ok_payload())
        doctor.run_inference(FAKE_KEY, model="some-model", send=send, sleep=no_sleep)
        self.assertEqual(len(send.calls), 1)
        call = send.calls[0]
        self.assertEqual(call["url"], "https://api.meta.ai/v1/chat/completions")
        self.assertEqual(call["timeout"], 20.0)
        self.assertEqual(call["headers"]["Authorization"], "Bearer " + FAKE_KEY)
        self.assertEqual(call["headers"]["Content-Type"], "application/json")
        body = json.loads(call["body"].decode("utf-8"))
        self.assertEqual(body["model"], "some-model")
        self.assertEqual(
            body["max_completion_tokens"], doctor.META_MAX_COMPLETION_TOKENS
        )
        self.assertTrue(body["messages"])


class UsageReporting(unittest.TestCase):
    def test_usage_reported_when_present(self):
        payload = _ok_payload()
        payload["usage"] = {
            "completion_tokens": 320,
            "prompt_tokens": 15,
            "total_tokens": 335,
            "completion_tokens_details": {"reasoning_tokens": 250},
        }
        send = fixed_send(200, payload)
        res = doctor.run_inference(FAKE_KEY, send=send, sleep=no_sleep)
        self.assertEqual(res["code"], doctor.OK)
        self.assertEqual(res["usage"]["completion_tokens"], 320)
        self.assertEqual(res["usage"]["reasoning_tokens"], 250)
        with mock.patch.dict(os.environ, {doctor.CREDENTIAL_ENV_VAR: FAKE_KEY}):
            with _pinned_plan():
                exit_code, report = doctor.run_doctor(send=send, sleep=no_sleep)
        self.assertEqual(exit_code, 0)
        self.assertIn("320 completion tokens (250 reasoning)", report)

    def test_usage_absent_when_not_reported(self):
        send = fixed_send(200, _ok_payload())
        res = doctor.run_inference(FAKE_KEY, send=send, sleep=no_sleep)
        self.assertEqual(res["code"], doctor.OK)
        self.assertIsNone(res["usage"]["completion_tokens"])
        self.assertIsNone(res["usage"]["reasoning_tokens"])
        with mock.patch.dict(os.environ, {doctor.CREDENTIAL_ENV_VAR: FAKE_KEY}):
            with _pinned_plan():
                _, report = doctor.run_doctor(send=send, sleep=no_sleep)
        self.assertNotIn("completion tokens", report)

    def test_usage_without_reasoning_breakdown(self):
        payload = _ok_payload()
        payload["usage"] = {"completion_tokens": 12}
        send = fixed_send(200, payload)
        with mock.patch.dict(os.environ, {doctor.CREDENTIAL_ENV_VAR: FAKE_KEY}):
            with _pinned_plan():
                _, report = doctor.run_doctor(send=send, sleep=no_sleep)
        self.assertIn("12 completion tokens", report)
        self.assertNotIn("reasoning", report)


class CredentialAndRedaction(unittest.TestCase):
    def test_missing_credential_unset(self):
        send = fixed_send(200, _ok_payload())
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(doctor.CREDENTIAL_ENV_VAR, None)
            with _pinned_plan():
                exit_code, report = doctor.run_doctor(send=send, sleep=no_sleep)
        self.assertEqual(exit_code, 2)
        self.assertIn("Meta inference: missing-credential", report)
        self.assertIn("Result: missing-credential", report)
        self.assertEqual(send.calls, [])  # no network attempt without a key

    def test_missing_credential_empty(self):
        send = fixed_send(200, _ok_payload())
        with mock.patch.dict(os.environ, {doctor.CREDENTIAL_ENV_VAR: "  "}):
            with _pinned_plan():
                exit_code, report = doctor.run_doctor(send=send, sleep=no_sleep)
        self.assertEqual(exit_code, 2)
        self.assertEqual(send.calls, [])

    def test_secret_never_in_ok_report(self):
        send = fixed_send(200, _ok_payload())
        with mock.patch.dict(os.environ, {doctor.CREDENTIAL_ENV_VAR: FAKE_KEY}):
            with _pinned_plan():
                exit_code, report = doctor.run_doctor(send=send, sleep=no_sleep)
        self.assertEqual(exit_code, 0)
        self.assertIn("Meta inference: ok", report)
        self.assertIn("Result: ok", report)
        self.assertNotIn(FAKE_KEY, report)

    def test_secret_redacted_from_error_detail(self):
        payload = _error_payload("key " + FAKE_KEY + " was rejected")
        send = sequence_send([(500, payload), (500, payload)])
        with mock.patch.dict(os.environ, {doctor.CREDENTIAL_ENV_VAR: FAKE_KEY}):
            with _pinned_plan():
                _, report = doctor.run_doctor(send=send, sleep=no_sleep)
        self.assertNotIn(FAKE_KEY, report)
        self.assertIn(doctor.REDACTED, report)

    def test_secret_redacted_from_network_exception(self):
        send = sequence_send(
            [
                doctor.NetworkFailure("TLS broke for " + FAKE_KEY),
                doctor.NetworkFailure("TLS broke for " + FAKE_KEY),
            ]
        )
        with mock.patch.dict(os.environ, {doctor.CREDENTIAL_ENV_VAR: FAKE_KEY}):
            with _pinned_plan():
                _, report = doctor.run_doctor(send=send, sleep=no_sleep)
        self.assertNotIn(FAKE_KEY, report)
        self.assertIn(doctor.REDACTED, report)

    def test_credential_line_shows_presence_only(self):
        send = fixed_send(200, _ok_payload())
        with mock.patch.dict(os.environ, {doctor.CREDENTIAL_ENV_VAR: FAKE_KEY}):
            with _pinned_plan():
                _, report = doctor.run_doctor(send=send, sleep=no_sleep)
        line = next(
            line
            for line in report.splitlines()
            if line.startswith("Meta credential:")
        )
        self.assertIn("present", line)
        self.assertNotIn(FAKE_KEY, line)


class DoctorGates(unittest.TestCase):
    def _plan(self, **overrides):
        plan = dict(PINNED_PLAN)
        plan.update(overrides)
        return mock.patch.object(doctor, "gather_plan_pin", return_value=plan)

    def test_interpreter_gate(self):
        send = fixed_send(200, _ok_payload())
        with mock.patch.dict(os.environ, {doctor.CREDENTIAL_ENV_VAR: FAKE_KEY}):
            with self._plan(), mock.patch.object(
                doctor, "check_interpreter", return_value=(False, "Python 3.9")
            ):
                exit_code, report = doctor.run_doctor(send=send, sleep=no_sleep)
        self.assertEqual(exit_code, doctor.EXIT_INTERPRETER)
        self.assertIn("Result: interpreter-unsupported", report)

    def test_plan_pin_gate_mismatch(self):
        send = fixed_send(200, _ok_payload())
        with mock.patch.dict(os.environ, {doctor.CREDENTIAL_ENV_VAR: FAKE_KEY}):
            with self._plan(head="0" * 40):
                exit_code, report = doctor.run_doctor(send=send, sleep=no_sleep)
        self.assertEqual(exit_code, doctor.EXIT_PLAN_PIN)
        self.assertIn("Result: plan-pin-failed", report)

    def test_plan_pin_gate_missing_repo(self):
        send = fixed_send(200, _ok_payload())
        missing = {
            "path": "/nonexistent",
            "head": None,
            "clean": False,
            "error": "directory not found",
        }
        with mock.patch.dict(os.environ, {doctor.CREDENTIAL_ENV_VAR: FAKE_KEY}):
            with mock.patch.object(
                doctor, "gather_plan_pin", return_value=missing
            ):
                exit_code, report = doctor.run_doctor(send=send, sleep=no_sleep)
        self.assertEqual(exit_code, doctor.EXIT_PLAN_PIN)

    def test_inference_failure_exit_code_passes_through(self):
        send = fixed_send(404, _error_payload("no such model"))
        with mock.patch.dict(os.environ, {doctor.CREDENTIAL_ENV_VAR: FAKE_KEY}):
            with self._plan():
                exit_code, report = doctor.run_doctor(send=send, sleep=no_sleep)
        self.assertEqual(exit_code, 7)
        self.assertIn("Result: model-not-available", report)

    def test_all_result_codes_exit_nonzero_except_ok(self):
        for code, expected in doctor.EXIT_CODES.items():
            with self.subTest(code=code):
                if code == doctor.OK:
                    self.assertEqual(expected, 0)
                else:
                    self.assertNotEqual(expected, 0)

    def test_plan_repo_path_override(self):
        with mock.patch.dict(os.environ, {doctor.PLAN_REPO_ENV_VAR: "/tmp/x"}):
            self.assertEqual(doctor.default_repo_path(), "/tmp/x")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(doctor.PLAN_REPO_ENV_VAR, None)
            self.assertEqual(doctor.default_repo_path(), doctor.PLAN_REPO_DEFAULT)


class PureChecks(unittest.TestCase):
    def test_interpreter_versions(self):
        for version in [(3, 12), (3, 12, 11), (3, 13), (3, 14, 0)]:
            with self.subTest(version=version):
                ok, _ = doctor.check_interpreter(version)
                self.assertTrue(ok)
        for version in [(3, 11), (3, 9, 1), (2, 7, 18)]:
            with self.subTest(version=version):
                ok, _ = doctor.check_interpreter(version)
                self.assertFalse(ok)

    def test_verify_plan_pin(self):
        ok, _ = doctor.verify_plan_pin("a" * 40, "a" * 40, True)
        self.assertTrue(ok)
        ok, _ = doctor.verify_plan_pin("a" * 40, "b" * 40, True)
        self.assertFalse(ok)
        ok, _ = doctor.verify_plan_pin("a" * 40, "a" * 40, False)
        self.assertFalse(ok)
        ok, _ = doctor.verify_plan_pin("a" * 40, None, False)
        self.assertFalse(ok)

    def test_gather_plan_pin_missing_directory(self):
        result = doctor.gather_plan_pin("/nonexistent-plan-repo-xyz")
        self.assertIsNone(result["head"])
        self.assertIsNotNone(result["error"])

    def test_report_only_helpers(self):
        self.assertTrue(doctor.git_info())
        self.assertTrue(doctor.disk_info())
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(doctor.BASENTEN_KEY_VAR, None)
            os.environ.pop(doctor.BASENTEN_MODEL_VAR, None)
            self.assertEqual(doctor.baseten_info(), "not configured")
        with mock.patch.dict(
            os.environ,
            {
                doctor.BASENTEN_KEY_VAR: "baseten-fake-key",
                doctor.BASENTEN_MODEL_VAR: "baseten-fake-model",
            },
        ):
            info = doctor.baseten_info()
            self.assertIn("configured", info)
            self.assertNotIn("baseten-fake-key", info)
            self.assertNotIn("baseten-fake-model", info)


class Cli(unittest.TestCase):
    def test_doctor_missing_key(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(doctor.CREDENTIAL_ENV_VAR, None)
            with _pinned_plan():
                buf = io.StringIO()
                with redirect_stdout(buf):
                    exit_code = cli.main(["doctor"])
        self.assertEqual(exit_code, 2)
        self.assertIn("Meta inference: missing-credential", buf.getvalue())

    def test_doctor_ok_with_fake_transport(self):
        send = fixed_send(200, _ok_payload())
        with mock.patch.dict(os.environ, {doctor.CREDENTIAL_ENV_VAR: FAKE_KEY}):
            with _pinned_plan(), mock.patch.object(
                doctor, "default_send", side_effect=send
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    exit_code = cli.main(["doctor"])
        self.assertEqual(exit_code, 0)
        self.assertIn("Meta inference: ok", buf.getvalue())
        self.assertNotIn(FAKE_KEY, buf.getvalue())

    def test_baseten_report_has_no_key_and_no_request(self):
        send = fixed_send(200, _ok_payload())
        canary = "baseten-canary-value-doctor-md0tnl"
        env = {doctor.CREDENTIAL_ENV_VAR: FAKE_KEY,
               "BASETEN_API_KEY": canary, "BASETEN_MODEL": "some-model"}
        with mock.patch.dict(os.environ, env):
            with _pinned_plan(), mock.patch.object(
                doctor, "default_send", side_effect=send
            ), mock.patch.object(
                socket, "socket", side_effect=OSError("network blocked")
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    exit_code = cli.main(["doctor"])
        self.assertEqual(exit_code, 0)
        self.assertIn("Baseten: configured", buf.getvalue())
        self.assertNotIn(canary, buf.getvalue())
        for call in send.calls:
            self.assertTrue(call["url"].startswith(doctor.META_API_BASE),
                            call["url"])

    def test_no_args_exits_two(self):
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            with self.assertRaises(SystemExit) as ctx:
                cli.main([])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
