# Guided app (M6 platform, Part 1)

`stargate app` starts the guided journey on the computer: nine steps
from Welcome to Done, calling M1 (scan), M3 (plan) and M4 (build)
in-process or through the heavy-step helper. Standard library only.

## Start it

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
cd /workspaces/m6
/usr/local/bin/python3 -m stargate app --port 8770 --forwarded-host \
  "$CODESPACE_NAME-8770.$GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN"
```

Forward port 8770 as **Private**, then open the printed
`https://<forwarded host>/start?token=...` link (the second line). The
Sign in button POSTs via fetch so the request always carries the
exact forwarded `https` origin; a refusal now logs its reason
(`app: refused ...`) without any token or cookie value.

## Rules (spec 1a, 1b)

- One session record holds the profile, answers and selection, each
  with its SHA-256. Every output records its input hashes; a screen
  shows an output only when they match, otherwise "out of date" with a
  re-run button. Details shows the output hash and its input hashes.
- Every change is a POST with the CSRF header. GET never changes
  anything. No CORS headers are ever sent. Bodies are at most 64 KB.
- Part 1 makes no live calls: Research shows "No recorded research for
  this phone yet", the install plan reads "added in Part 2", Go live
  stays disabled, and the phone's page link arrives in Part 2.
- Builds are fresh only, into `/workspaces/m6-tools/out/`, through
  `/workspaces/m6-tools/ahead-heavy.sh` under the shared build lock.
  Nothing is reused; nothing writes to `/workspaces/m4a-tools/out/`.
