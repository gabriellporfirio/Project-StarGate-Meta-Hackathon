"""M2 run budgets: counted by code, checked before every request.

Meta: at most 12 calls per run (attempts included), 2048 completion
tokens per call, 20,000 completion tokens total, 24,000 source-text
chars per call, 60,000 total. HTTP: 30 requests per run; GitHub 10
core + 5 search. Whole run: 180 s. When a budget runs out the run
stops and the report is marked partial -- except a low GitHub
allowance, which only stops GitHub calls (those facts become unknown
with rate_limited) while the rest of the run continues.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

META_MAX_CALLS = 12
META_MAX_COMPLETION_PER_CALL = 2048
META_MAX_COMPLETION_TOTAL = 20000
META_MIN_COMPLETION_LEFT = 256
INPUT_MAX_CHARS_PER_CALL = 24000
INPUT_MAX_CHARS_TOTAL = 60000
HTTP_MAX_REQUESTS = 30
GITHUB_MAX_CORE = 10
GITHUB_MAX_SEARCH = 5
GITHUB_LOW_REMAINING = 2
RUN_DEADLINE_SECONDS = 180.0


class BudgetExhausted(Exception):
    """Raised when a run budget runs out; carries a short reason."""

    def __init__(self, reason: str) -> None:
        super().__init__("budget exhausted: %s" % reason)
        self.reason = reason


@dataclass
class RunBudgets:
    meta_calls_used: int = 0
    completion_used: int = 0
    input_chars_used: int = 0
    http_used: int = 0
    github_core_used: int = 0
    github_search_used: int = 0
    github_remaining: int | None = None
    github_stopped: bool = False
    monotonic: object = field(default_factory=lambda: time.monotonic)
    start: float | None = None

    def __post_init__(self) -> None:
        if self.start is None:
            self.start = self.monotonic()

    def elapsed(self) -> float:
        return self.monotonic() - self.start

    def check_time(self) -> None:
        if self.elapsed() >= RUN_DEADLINE_SECONDS:
            raise BudgetExhausted("deadline")

    def peek_meta_allowance(self) -> int:
        """Check a Meta request is affordable without counting it.

        Returns the completion allowance. Raises BudgetExhausted when
        the next attempt cannot run.
        """
        self.check_time()
        if self.meta_calls_used >= META_MAX_CALLS:
            raise BudgetExhausted("meta-calls")
        left = META_MAX_COMPLETION_TOTAL - self.completion_used
        if left < META_MIN_COMPLETION_LEFT:
            raise BudgetExhausted("meta-tokens")
        return min(META_MAX_COMPLETION_PER_CALL, left)

    def begin_meta_attempt(self) -> int:
        """Check before one Meta request (initial or retry).

        Returns the completion allowance for this request: the per-call
        cap lowered to whatever is left. Raises BudgetExhausted when
        too little is left, and counts the attempt immediately.
        """
        allowance = self.peek_meta_allowance()
        self.meta_calls_used += 1
        return allowance

    def note_meta_usage(self, usage: dict) -> None:
        completion = (usage or {}).get("completion_tokens")
        if isinstance(completion, int) and not isinstance(completion, bool):
            self.completion_used += max(0, completion)

    def fit_sources(self, newest_first: list) -> str:
        """Join newest source texts first within the per-call and total
        caps. Older text is dropped by code, never by the model."""
        per_call_left = INPUT_MAX_CHARS_PER_CALL
        total_left = INPUT_MAX_CHARS_TOTAL - self.input_chars_used
        kept: list[str] = []
        used = 0
        for text in newest_first:
            if not isinstance(text, str) or not text:
                continue
            room = min(per_call_left - used, total_left - used)
            if room <= 0:
                break
            kept.append(text[:room])
            used += min(len(text), room)
        self.input_chars_used += used
        return "\n\n".join(reversed(kept))

    def github_usable(self) -> bool:
        if self.github_stopped:
            return False
        if self.github_remaining is not None:
            if self.github_remaining <= GITHUB_LOW_REMAINING:
                self.github_stopped = True
                return False
        return True

    def check_http(self, kind: str) -> None:
        """Check before one HTTP request hop. kind is github-core,
        github-search or other."""
        self.check_time()
        if self.http_used >= HTTP_MAX_REQUESTS:
            raise BudgetExhausted("http")
        if kind == "github-core" and self.github_core_used >= GITHUB_MAX_CORE:
            raise BudgetExhausted("github-core")
        if kind == "github-search":
            if self.github_search_used >= GITHUB_MAX_SEARCH:
                raise BudgetExhausted("github-search")

    def note_http(self, kind: str, headers: dict | None = None) -> None:
        self.http_used += 1
        if kind == "github-core":
            self.github_core_used += 1
        elif kind == "github-search":
            self.github_search_used += 1
        if headers:
            remaining = headers.get("x-ratelimit-remaining")
            try:
                value = int(str(remaining))
            except (TypeError, ValueError):
                return
            if self.github_remaining is None or value < self.github_remaining:
                self.github_remaining = value
