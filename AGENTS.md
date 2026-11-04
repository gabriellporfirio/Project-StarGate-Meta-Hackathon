# AGENTS.md — StarGate product repo (M0)

- Build only what the pinned plan allows. Plan: `gabriellporfirio/Project-Stargate`
  at `b498e7037e1e064fd6db8ff73687ecc5a81e449b` (see `docs/decisions.md`).
  No product features in M0.
- Python 3.12+, standard library only. Environment lives in the project:
  `python3 -m venv .venv`, then `.venv/bin/pip install -e .`.
- Run tests with `.venv/bin/python -m unittest -v`. Tests stay offline.
- `MODEL_API_KEY` comes from the environment only. Never print, log, or write
  it; keep it out of files, prompts, and command lines (literal `$MODEL_API_KEY`,
  never the value).
- Never push. Checkpoint commits stay local until the owner approves a push.
- Cloud workspace only: nothing on the Mac, no phone, no private StarGate/VPN work.
