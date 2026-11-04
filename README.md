# StarGate
Turns old Android phones into private home servers. Meta hackathon project. Plan: Project-Stargate (private).

StarGate inspects an old Android phone, researches what community
software supports it, and drafts a plan to turn it into a small
private server (file storage plus an on-device assistant) instead of
landfill.

## Demo scope

The demo uses SIMULATED phone profiles (fixtures, every value
labelled SIMULATED) and the Android Emulator. No real handset is
flashed: plans only ever allow `emulator_only` builds
(`stargate/plan.py:43`), and real-device access raises instead of
running (`stargate/probe.py:265`).

## Demo commands

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/stargate doctor
.venv/bin/stargate scan --sim flagship
.venv/bin/stargate plan --sim flagship --services files \
  --research fixtures/research/flagship.json
.venv/bin/stargate install-plan --sim flagship
```

Sources: `stargate/cli.py:27` (doctor), `stargate/cli.py:31`
(scan, sims at `stargate/cli.py:37`), `stargate/cli.py:55` (plan),
`stargate/cli.py:65` (build, needs `--plan` and `--out`),
`stargate/cli.py:71` (install-plan).

`MODEL_API_KEY` comes from the environment only and is never written to a file.
See `docs/decisions.md` for the M0 record.

## How to run the platform app

`stargate app` starts the guided journey on the computer: nine steps
from Welcome to Done, calling M1 (scan), M3 (plan) and M4 (build).
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

## Models and keys

The install decision uses Muse Spark (`muse-spark-1.3`,
`stargate/doctor.py:26`, `stargate/install.py:356`). The Assistant's
optional cloud mode uses Muse Spark Open on Baseten
(`meta-models/muse-spark-open-1026` on `inference.baseten.co`,
`stargate/m4c.py:28-37`). Users bring their own key
(`stargate/m4c.py:7`).

[PENDING: proof] Tonight's live M5 install decision for the demo
phone. [PENDING: proof] Tonight's live cloud answer from the
Assistant.

## Credits and licence

Meta's Muse Code CLI wrote the product code. People are listed in
`CREDITS.md`. The project's original code is MIT-licensed (see
`LICENSE`). Bundled third-party material keeps its own licence (see
`third_party/NOTICES.md`); the root MIT file does not relicense it.
