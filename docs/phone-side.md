# The phone side

What runs on the phone, how it is built, and what has and has not been
tested. Written for a non-specialist reader.

## What the phone runs

One small app (about 144 KB without a model). It is two things:

- **The home screen.** A clock, a status line and one tile per chosen
  service, plus Status. Each tile opens settings and controls only:
  start, stop, restart, the access code, storage, the assistant's
  model switch and key, lock the vault, reboot. Nothing is used on the
  phone's own screen.
- **One server**, listening on the phone's own address only
  (127.0.0.1). A computer reaches it through the cable.

Open `http://127.0.0.1:8080` on the computer, type the six-digit code
shown on the phone (Files screen), and you get the management page:

| Service | On the management page |
| --- | --- |
| Files and media | Drop files in, download, delete, view photos, play music and video |
| AI assistant | Chat. On the phone's own model, or through your own cloud key |
| Password vault | Entries sealed with one master password (AES-256-GCM, key from PBKDF2). Locks after five quiet minutes. The master password is never stored and cannot be recovered |
| Private VPN | Not built. Shown greyed out as a future option |

Only the services chosen in the platform exist in a build.

## How to build it

```
sh scripts/setup-android-tools.sh          # once, Debian or Ubuntu
python3 -m stargate.build.hubapk --doctor   # shows what was found
python3 -m stargate app --port 8770         # the guided platform
```

The platform's Build step makes a folder with the app, a
`build-manifest.json` (hashes) and `INSTALL.txt` (exact install and
undo commands). To look at the phone's pages without a phone:

```
python3 -m unittest tests.test_phonebuild   # includes the 55 server checks
```

## Install routes

- **Takeover (works without unlocking).** Four adb commands install
  the app, make it the home screen and link its page to the computer.
  Undo is one uninstall. Nothing is erased. See `stargate/takeover.py`.
- **Clean system.** `python3 -m stargate.build.cleanimage` copies a
  base system image for the exact phone model, removes the apps on its
  removal list (browser, camera, gallery, stock home screens and so
  on) and adds the app as the only home screen. `--dry-run` lists what
  would go and what would stay. Flashing is a separate per-phone step
  that no tool here performs.

## Tested, and not yet tested

Tested here (no phone or emulator was available in this workspace):

- The phone's server: 55 checks over real sockets (sign-in, files,
  media ranges, vault encryption, refusals).
- Both pages in a real browser, portrait and landscape.
- The app compiles, packages and signs; its baked config is checked
  against the plan on every build.
- The clean-image builder on a small made-up ext4 image.

Not yet tested, so treat as unproven until tried on a spare phone:

- The app actually running on Android (home screen, service staying
  alive with the screen off, start after reboot).
- The takeover commands on a real phone.
- The clean-image builder on a real base image, and booting its result.
- The on-phone model inside this app (needs a phone-ready model bundle
  and the model runtime; without them a build is cloud-only and says so).

Known limits: reach is by cable only (no Wi-Fi access to the page);
the Wi-Fi switch on Status only works where Android grants the
network-settings permission, otherwise it shows the state; there is no
update check; the takeover route does not yet switch off the phone's
other apps.
