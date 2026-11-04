"""No branding in the platform UI (M6 Part 1, spec 5).

Scans the served UI files for "StarGate" and "Spare" (case-insensitive
whole words). The logo mark appears only in the platform header.

Note: the Android app labels still carry the old name
(android/launcher "StarGate", android/files "StarGate Files"); relabelling
the phone side belongs to Phone Part A, which owns the launcher and the
manifests. This scan covers the platform UI files.
"""
from __future__ import annotations

import os
import re
import unittest

APP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "stargate", "app")

SERVED_TEXT_FILES = ("index.html", "signin.html", "signin.js", "app.css",
                       "app.js")

BRAND_RE = re.compile(r"\b(stargate|spare)\b", re.IGNORECASE)


def _read(name):
    with open(os.path.join(APP_DIR, name), encoding="utf-8") as handle:
        return handle.read()


class BrandingTests(unittest.TestCase):
    def test_no_brand_words_in_served_ui(self):
        for name in SERVED_TEXT_FILES + ("LOGO-SOURCE.txt",):
            text = _read(name)
            match = BRAND_RE.search(text)
            self.assertIsNone(match, "%s: %r" % (name, match))

    def test_logo_only_in_platform_header(self):
        index = _read("index.html")
        self.assertEqual(index.count("/logo.png"), 1)
        self.assertIn("<header", index.split("/logo.png")[0])
        signin = _read("signin.html")
        self.assertNotIn("logo", signin.lower())
        self.assertNotIn("<img", signin.lower())

    def test_logo_is_a_real_png(self):
        with open(os.path.join(APP_DIR, "logo-light.png"),
                  "rb") as handle:
            magic = handle.read(8)
        self.assertEqual(magic, b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
