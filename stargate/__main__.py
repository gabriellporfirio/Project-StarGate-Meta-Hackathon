"""Entry point for `python -m stargate` (same as the stargate CLI)."""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
