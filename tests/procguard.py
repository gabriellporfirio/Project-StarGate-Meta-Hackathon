"""Guards that fail a test if product code tries to start a process.

Import inventory (how each stargate/ module reaches process spawning):
- stargate.doctor does ``import subprocess`` (uses subprocess.run) and
  ``import os`` (uses environ.get, path.isdir only).
- stargate.probe does ``import os`` (uses environ.get only).
- stargate.cli, stargate.scan, stargate.profile and stargate.install
  import nothing that can start a process (argparse, dataclasses,
  datetime, hashlib, json, re, time, zlib, pathlib, sys.argv use
  only). Nothing imports asyncio, pty or multiprocessing.

All product imports are ``import x`` style, so the name the code looks
up (e.g. stargate.probe.os.system) IS the standard-library attribute;
targets below spell the module lookup site first and the standard
library as a backstop, deduplicated by resolved object. Every patch
raises instead of running.
"""
from __future__ import annotations

import contextlib
import importlib
from unittest import mock

_SUBPROCESS_FUNCS = ("run", "Popen", "call", "check_call", "check_output")
_OS_FUNCS = (
    "system",
    "popen",
    "spawnl",
    "spawnle",
    "spawnlp",
    "spawnlpe",
    "spawnv",
    "spawnve",
    "spawnvp",
    "spawnvpe",
    "posix_spawn",
    "posix_spawnp",
    "execl",
    "execle",
    "execlp",
    "execv",
    "execve",
    "execvp",
    "execvpe",
)

_MODULE_SITES = (
    "stargate.doctor.subprocess",
    "stargate.doctor.os",
    "stargate.probe.os",
)


def _refuse(*args, **kwargs):
    raise AssertionError("refused: product code tried to start a process")


def _resolve(dotted):
    parts = dotted.split(".")
    for cut in range(len(parts), 0, -1):
        try:
            module = importlib.import_module(".".join(parts[:cut]))
        except ImportError:
            continue
        target = module
        for attr in parts[cut:]:
            target = getattr(target, attr)
        return target
    raise ImportError("cannot resolve %s" % dotted)


def guard_targets():
    """Patch target strings, deduplicated by resolved object."""
    targets = []
    for site in _MODULE_SITES:
        funcs = _SUBPROCESS_FUNCS if site.endswith("subprocess") else _OS_FUNCS
        for func in funcs:
            targets.append("%s.%s" % (site, func))
    for func in _SUBPROCESS_FUNCS:
        targets.append("subprocess.%s" % func)
    for func in _OS_FUNCS:
        targets.append("os.%s" % func)
    unique = []
    seen = set()
    for target in targets:
        holder_name, _, attr = target.rpartition(".")
        try:
            holder = _resolve(holder_name)
        except ImportError:
            continue
        if not hasattr(holder, attr):
            continue
        key = (id(holder), attr)
        if key in seen:
            continue
        seen.add(key)
        unique.append(target)
    return unique


@contextlib.contextmanager
def process_guards():
    """Patch every process-spawning lookup so that calling one raises.

    Yields the list of mock objects so tests can also assert none of
    them was called.
    """
    mocks = []
    with contextlib.ExitStack() as stack:
        for target in guard_targets():
            mocks.append(
                stack.enter_context(mock.patch(target, side_effect=_refuse))
            )
        yield mocks
