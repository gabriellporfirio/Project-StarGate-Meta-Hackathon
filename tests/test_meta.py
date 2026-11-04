"""Tests for the Meta chat client (offline, fake transport)."""
from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from stargate import meta

FAKE_KEY = "test-fake-key-for-meta-tests"


def _envelope(message, finish="stop", usage=None):
    return {"id": "chatcmpl-test", "model": "muse-spark-1.3",
            "choices": [{"message": message, "finish_reason": finish}],
            "usage": usage or {"completion_tokens": 10,
                               "prompt_tokens": 5,
                               "completion_tokens_details": {
                                   "reasoning_tokens": 7}}}


class ChatParsing(unittest.TestCase):
    def _run(self, body, key=FAKE_KEY):
        send = mock.MagicMock(return_value=(200, json.dumps(body).encode()))
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": key}):
            result = meta.chat([{"role": "user", "content": "hi"}],
                               max_completion_tokens=64, send=send,
                               sleep=lambda seconds: None)
        return result, send

    def test_tool_call_parses(self):
        body = _envelope({"role": "assistant", "content": "looking up",
                          "tool_calls": [
                              {"id": "call_1", "type": "function",
                               "function": {"name": "github_repo",
                                            "arguments": '{"repo": "x"}'}}]},
                         finish="tool_calls")
        result, _ = self._run(body)
        self.assertEqual(len(result.tool_calls), 1)
        call = result.tool_calls[0]
        self.assertEqual(call.name, "github_repo")
        self.assertEqual(call.arguments, {"repo": "x"})
        self.assertEqual(result.finish_reason, "tool_calls")
        self.assertEqual(result.attempts, 1)

    def test_key_never_in_echo(self):
        body = _envelope({"role": "assistant", "content": "done"})
        result, _ = self._run(body)
        blob = json.dumps(result.request_echo)
        self.assertNotIn(FAKE_KEY, blob)
        self.assertIn("***", blob)

    def test_empty_answer_is_bad_response(self):
        body = _envelope({"role": "assistant", "content": ""},
                         finish="length")
        send = mock.MagicMock(
            return_value=(200, json.dumps(body).encode()))
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": FAKE_KEY}):
            with self.assertRaises(meta.MetaError) as raised:
                meta.chat([{"role": "user", "content": "hi"}],
                          max_completion_tokens=64, send=send,
                          sleep=lambda seconds: None)
        self.assertEqual(raised.exception.code, "bad-response")
        self.assertIn("length", raised.exception.detail)

    def test_bad_tool_arguments_kept_raw(self):
        body = _envelope({"role": "assistant", "content": "",
                          "tool_calls": [
                              {"id": "call_1", "type": "function",
                               "function": {"name": "github_repo",
                                            "arguments": "{oops}"}}]},
                         finish="tool_calls")
        result, _ = self._run(body)
        self.assertIsNone(result.tool_calls[0].arguments)
        self.assertEqual(result.tool_calls[0].arguments_raw, "{oops}")

    def test_usage_parsing(self):
        result, _ = self._run(
            _envelope({"role": "assistant", "content": "done"}))
        self.assertEqual(result.usage["completion_tokens"], 10)
        self.assertEqual(result.usage["reasoning_tokens"], 7)
        self.assertEqual(result.usage["prompt_tokens"], 5)


class RetriesAndErrors(unittest.TestCase):
    def test_retry_then_success(self):
        body = _envelope({"role": "assistant", "content": "done"})
        send = mock.MagicMock(side_effect=[
            (500, b"boom"), (200, json.dumps(body).encode())])
        slept = []
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": FAKE_KEY}):
            result = meta.chat([{"role": "user", "content": "hi"}],
                               max_completion_tokens=64, send=send,
                               sleep=slept.append)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(slept, [1.0])

    def test_empty_answer_retried_once(self):
        empty = _envelope({"role": "assistant", "content": ""},
                          finish="length")
        full = _envelope({"role": "assistant", "content": "done"})
        send = mock.MagicMock(side_effect=[
            (200, json.dumps(empty).encode()),
            (200, json.dumps(full).encode())])
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": FAKE_KEY}):
            result = meta.chat([{"role": "user", "content": "hi"}],
                               max_completion_tokens=64, send=send,
                               sleep=lambda seconds: None)
        self.assertEqual(result.content, "done")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(send.call_count, 2)

    def test_empty_answer_twice_is_bad_response(self):
        empty = _envelope({"role": "assistant", "content": ""},
                          finish="length")
        send = mock.MagicMock(
            return_value=(200, json.dumps(empty).encode()))
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": FAKE_KEY}):
            with self.assertRaises(meta.MetaError) as raised:
                meta.chat([{"role": "user", "content": "hi"}],
                          max_completion_tokens=64, send=send,
                          sleep=lambda seconds: None)
        self.assertEqual(raised.exception.code, "bad-response")
        self.assertEqual(send.call_count, 2)

    def test_no_retry_on_400(self):
        send = mock.MagicMock(return_value=(400, b"bad"))
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": FAKE_KEY}):
            with self.assertRaises(meta.MetaError) as raised:
                meta.chat([{"role": "user", "content": "hi"}],
                          max_completion_tokens=64, send=send,
                          sleep=lambda seconds: None)
        self.assertEqual(raised.exception.code, "bad-response")
        self.assertEqual(send.call_count, 1)

    def test_status_mapping(self):
        for status, code in ((401, "auth-failed"), (404, "model-not-available"),
                             (408, "network-error")):
            with self.subTest(status=status):
                send = mock.MagicMock(return_value=(status, b"x"))
                with mock.patch.dict(os.environ, {"MODEL_API_KEY": FAKE_KEY}):
                    with self.assertRaises(meta.MetaError) as raised:
                        meta.chat([{"role": "user", "content": "hi"}],
                                  max_completion_tokens=64, send=send,
                                  sleep=lambda seconds: None)
                self.assertEqual(raised.exception.code, code)

    def test_rate_limited_retried_once(self):
        send = mock.MagicMock(return_value=(429, b"slow"))
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": FAKE_KEY}):
            with self.assertRaises(meta.MetaError) as raised:
                meta.chat([{"role": "user", "content": "hi"}],
                          max_completion_tokens=64, send=send,
                          sleep=lambda seconds: None)
        self.assertEqual(raised.exception.code, "quota-or-rate-limited")
        self.assertEqual(send.call_count, 2)

    def test_missing_key_sends_nothing(self):
        send = mock.MagicMock()
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": ""}):
            with self.assertRaises(meta.MetaError) as raised:
                meta.chat([{"role": "user", "content": "hi"}],
                          max_completion_tokens=64, send=send,
                          sleep=lambda seconds: None)
        self.assertEqual(raised.exception.code, "missing-credential")
        send.assert_not_called()

    def test_before_attempt_runs_per_attempt(self):
        body = _envelope({"role": "assistant", "content": "done"})
        send = mock.MagicMock(side_effect=[
            (500, b"boom"), (200, json.dumps(body).encode())])
        seen = []
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": FAKE_KEY}):
            meta.chat([{"role": "user", "content": "hi"}],
                      max_completion_tokens=64, send=send,
                      sleep=lambda seconds: None,
                      before_attempt=lambda: seen.append(1))
        self.assertEqual(seen, [1, 1])

    def test_before_attempt_aborts(self):
        class Stop(Exception):
            pass

        def _stop():
            raise Stop()

        send = mock.MagicMock()
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": FAKE_KEY}):
            with self.assertRaises(Stop):
                meta.chat([{"role": "user", "content": "hi"}],
                          max_completion_tokens=64, send=send,
                          sleep=lambda seconds: None,
                          before_attempt=_stop)
        send.assert_not_called()

    def test_network_error_after_retry(self):
        send = mock.MagicMock(side_effect=OSError("down"))
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": FAKE_KEY}):
            with self.assertRaises(meta.MetaError) as raised:
                meta.chat([{"role": "user", "content": "hi"}],
                          max_completion_tokens=64, send=send,
                          sleep=lambda seconds: None)
        self.assertEqual(raised.exception.code, "network-error")
        self.assertEqual(send.call_count, 2)


if __name__ == "__main__":
    unittest.main()
