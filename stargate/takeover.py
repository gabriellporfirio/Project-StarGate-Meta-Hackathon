"""Take over a phone's own Android: install, and undo, over the cable.

This is the route for phones that cannot be reflashed. Nothing is
unlocked and nothing is erased: the phone app is installed next to
what is already there, made the home screen, and its page is
forwarded to this computer through the cable. Undo removes the app
again and the phone's old home screen comes back by itself.

Every step is a fixed adb command (listed by `install_steps` and
`undo_steps`), so what will run can be read before anything runs.
A serial number is always required: nothing here ever talks to
"whichever phone is plugged in".
"""
from __future__ import annotations

import os
import re
import subprocess

PACKAGE = "com.stargate.launcher"
HOME_ACTIVITY = PACKAGE + "/com.stargate.hub.HomeActivity"
PORT = 8080
MODEL_DIR = "/sdcard/Android/data/%s/files/model" % PACKAGE
_SERIAL_RE = re.compile(r"[A-Za-z0-9._:-]{1,64}\Z")


class TakeoverError(Exception):
    """A takeover step could not run; the message says which."""


def install_steps(apk_path: str, model_files=None, port: int = PORT) -> list:
    """The install, as ordered steps: {title, why, argv}."""
    steps = [
        {"title": "Install the phone app",
         "why": "Adds the one app. Nothing on the phone is removed.",
         "argv": ["install", "-r", apk_path]},
    ]
    if model_files:
        steps.append({
            "title": "Make room for the model",
            "why": "A folder only the phone app reads.",
            "argv": ["shell", "mkdir", "-p", MODEL_DIR]})
        for path in model_files:
            steps.append({
                "title": "Copy %s" % os.path.basename(path),
                "why": "The model that runs on the phone. The app "
                       "checks it before loading it.",
                "argv": ["push", path, MODEL_DIR + "/"
                         + os.path.basename(path)]})
    steps += [
        {"title": "Make it the home screen",
         "why": "The phone now opens straight to its services.",
         "argv": ["shell", "cmd", "package", "set-home-activity",
                  HOME_ACTIVITY]},
        {"title": "Open it",
         "why": "Starts the services.",
         "argv": ["shell", "am", "start", "-n", HOME_ACTIVITY]},
        {"title": "Link the phone's page to this computer",
         "why": "Opens http://127.0.0.1:%d on this computer, through "
                "the cable only." % port,
         "argv": ["forward", "tcp:%d" % port, "tcp:%d" % PORT]},
    ]
    return steps


def undo_steps(port: int = PORT) -> list:
    """Put the phone back: the app and everything it stored go away."""
    return [
        {"title": "Unlink the phone's page",
         "why": "Closes the cable link.",
         "argv": ["forward", "--remove", "tcp:%d" % port]},
        {"title": "Remove the phone app",
         "why": "Removes the app with the files, passwords and key it "
                "stored. The phone's old home screen comes back.",
         "argv": ["uninstall", PACKAGE]},
    ]


def steps_text(steps: list, serial: str = "SERIAL") -> str:
    """The steps as numbered plain text with the exact commands."""
    lines = []
    for number, step in enumerate(steps, 1):
        lines.append("%d. %s" % (number, step["title"]))
        lines.append("   %s" % step["why"])
        lines.append("   adb -s %s %s" % (serial, " ".join(
            _quote(part) for part in step["argv"])))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _quote(part: str) -> str:
    return part if re.match(r"[A-Za-z0-9_./:=+-]+\Z", part) \
        else "'%s'" % part.replace("'", "'\\''")


def run(serial: str, steps: list, *, adb_path: str = "adb",
        runner=None, say=None) -> list:
    """Run the steps on one named phone; stops at the first failure."""
    if not isinstance(serial, str) or not _SERIAL_RE.match(serial):
        raise TakeoverError("a phone serial number is required")
    runner = runner or _run
    say = say or (lambda line: None)
    done = []
    for step in steps:
        say(step["title"])
        code, output = runner([adb_path, "-s", serial] + step["argv"])
        done.append({"title": step["title"], "exit": code})
        if code != 0:
            raise TakeoverError("%s failed: %s" % (
                step["title"], (output or "").strip()[-400:]))
    return done


def _run(argv: list):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=600)
    except (OSError, subprocess.TimeoutExpired) as failed:
        return 1, str(failed)
    return proc.returncode, proc.stdout + proc.stderr
