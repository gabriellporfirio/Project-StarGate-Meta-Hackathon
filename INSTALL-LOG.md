# INSTALL-LOG.md — codespace installs (M0)

Codespace image: `mcr.microsoft.com/devcontainers/python:1-3.12-bookworm`.
No system packages were installed. Pre-existing tools (verified, not installed):

| Tool | Version | Source |
|---|---|---|
| python3 | 3.12.11 at `/usr/local/bin/python3` | devcontainer image |
| pip | 25.0.1 (python 3.12) | devcontainer image |
| git | 2.50.1 | devcontainer image |
| venv | standard library | devcontainer image |

## Bootstrap commands (approved and ran 30 Oct 2026, ~16:24)

1. `python3 -m venv .venv` — DONE. Created `.venv` from
   `/usr/local/bin/python3` (3.12.11); venv pip 25.0.1. Downloads nothing.
2. `.venv/bin/pip install -e .` — DONE, exit 0. Editable install of this
   package (no dependencies) as `stargate-0.0.0`. The isolated build
   environment downloaded one wheel (see below).

## pip downloads (wheel bytes observed in pip's HTTP cache)

pip's console output only said "Installing build dependencies: ... done"
(per-package lines are hidden at default verbosity), so the download below
is identified from the actual wheel bytes pip cached: `~/.cache/pip` was
created fresh by this install, and contains exactly one wheel.

| Name | Version | Source |
|---|---|---|
| setuptools | 84.2.1 | PyPI default index (`https://pypi.org/simple`; `pip config list` empty) |

The remaining cached bodies are PyPI index/metadata pages (not installed)
and pip's self-version check. Nothing else was downloaded or installed.

## Mac

Nothing installed on the Mac.

## M1

M1: no installs
