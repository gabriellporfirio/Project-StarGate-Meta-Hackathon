"""Marks for tests that need the original hackathon build workspace.

A few older tests read tools and built files that were never part of
this repository (a JDK, an Android SDK and built apps kept in sibling
folders such as /workspaces/m4-tools). Where those folders exist the
tests run as before; anywhere else (a fresh clone, GitHub's test
runner) they are reported as skipped, with this reason, instead of
failing for a reason that has nothing to do with the code.
"""
import os
import unittest

_REPO_PARENT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
REASON = ("needs the original hackathon build workspace "
          "(tools and built apps that are not in this repository)")


def _has(*names):
    return all(
        os.path.isdir(os.path.join("/workspaces", name))
        or os.path.isdir(os.path.join(_REPO_PARENT, name))
        for name in names)


needs_build_tools = unittest.skipUnless(_has("m4-tools"), REASON)
needs_built_apps = unittest.skipUnless(
    _has("m4-tools", "m4a-tools", "m4b-tools"), REASON)
needs_run_scripts = unittest.skipUnless(
    os.path.isfile("/workspaces/m5-tools/v4-run.sh"), REASON)
