# StarGate

[![Tests](https://github.com/gabriellporfirio/Project-StarGate-Meta-Hackathon/actions/workflows/tests.yml/badge.svg)](https://github.com/gabriellporfirio/Project-StarGate-Meta-Hackathon/actions/workflows/tests.yml)

Turns old Android phones into private home servers, instead of landfill.

You plug in an old phone, a guided app on your computer checks what it
can honestly run, you pick the services you want, and it builds the
phone's software for exactly those choices:

| Service | What you get |
| --- | --- |
| Files and media | Your photos, documents, music and video kept on the phone |
| AI assistant | Ask questions in plain words, on the phone's own model or through your own cloud key |
| Password vault | Your passwords sealed with one master password |
| Private VPN | Not built yet (shown greyed out as a future option) |

The phone itself stays bare: its screen shows only settings and
controls. Everything you actually use opens from the phone's page on
your computer. A phone is refused only when nothing at all can run on it.

This started as a Meta hackathon project (submitted 5 November 2026)
and has been extended since. The submitted version is kept under the
tag `hackathon-submission`.

## Try it

The quickest way needs no phone and nothing installed on your
computer: open the repository in a GitHub Codespace (green **Code**
button, **Codespaces**, **Create codespace**), then in its terminal:

```sh
sh scripts/setup-android-tools.sh
python3 -m stargate app --port 8770 --forwarded-host \
  "$CODESPACE_NAME-8770.$GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN"
```

Open the `https://` start link it prints and press Sign in. Pick one
of the example phones, answer two questions, choose services and
press Build.

On your own Debian or Ubuntu computer it is the same, with a shorter
command (`python3 -m stargate app --port 8770`, then open the
`http://127.0.0.1` link). It needs Python 3.12 or newer and nothing
else from Python: standard library only. The setup script installs the
few command-line tools the phone app is built with; everything up to
the Build step works without it.

## Run the tests

```sh
python3 -m unittest discover -s tests -t .
```

The same command runs on GitHub for every push (the badge above).
Some older tests need the original hackathon build workspace (tools
and built apps that were never part of this repository); anywhere
else they are reported as skipped, with that reason.

## What is tested, and what is not

The phone's software has been built and checked on a computer only.
It has **not yet run on a real phone**. The honest list of what has
and has not been tried is in [docs/phone-side.md](docs/phone-side.md).
Do not use it on a phone you depend on.

## The phone side

What runs on the phone, how it is built, and the two install routes
(keep the phone's Android and take it over, or a clean system): see
[docs/phone-side.md](docs/phone-side.md).

## More on the platform app

`stargate app` starts the guided journey on the computer: nine steps
from Welcome to Done, from scanning the phone to building its software.
Standard library only. Full rules in `docs/app.md`.

```sh
stargate app [--port 8770] [--forwarded-host <host>]
```

The server listens on `127.0.0.1` only (default port `8770`, printed at
start). Forward that port as **Private**, then open the one-time start
link printed in the terminal. The link shows a Sign in button; the
button's POST sets the session cookie and consumes the link.

`--forwarded-host` is the codespace's forwarded host (bare host, no
scheme). It is the only non-loopback Host accepted, and the session
cookie is Secure over it. From a Mac browser (Safari included):

```sh
python3 -m stargate app --port 8770 --forwarded-host \
  "$CODESPACE_NAME-8770.$GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN"
```

Forward port 8770 as **Private**, then open the printed
`https://<forwarded host>/start?token=...` link. The Sign in button
POSTs via fetch so the request always carries the exact forwarded
`https` origin; a refusal logs its reason (`app: refused ...`)
without any token or cookie value.

## The hackathon version

The submitted demo used simulated phone profiles and the Android
Emulator, with these commands (they still work):

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/stargate doctor
.venv/bin/stargate scan --sim flagship
.venv/bin/stargate plan --sim flagship --services files \
  --research fixtures/research/flagship.json
.venv/bin/stargate install-plan --sim flagship
```

`MODEL_API_KEY` comes from the environment only and is never written
to a file. See `docs/decisions.md` for the early decisions.

## Models and keys

The install decision uses Muse Spark (`muse-spark-1.3`,
`stargate/doctor.py:26`, `stargate/install.py:356`). The Assistant's
optional cloud mode uses Muse Spark Open on Baseten
(`meta-models/muse-spark-open-1026` on `inference.baseten.co`,
`stargate/m4c.py:28-37`). Users bring their own key
(`stargate/m4c.py:7`).

## Credits and licence

During the hackathon, Meta's Muse Code CLI wrote the product code.
Work after the hackathon (the git history from 9 November 2026: the
phone app, the password vault, the redesigned platform and the build
steps for real phones) was written with Claude Code. People are listed
in `CREDITS.md`. The project's original code is MIT-licensed (see
`LICENSE`). Bundled third-party material keeps its own licence (see
`third_party/NOTICES.md`); the root MIT file does not relicense it.
