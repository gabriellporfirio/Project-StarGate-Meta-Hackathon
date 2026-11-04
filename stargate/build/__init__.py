"""StarGate image build (M4): plan gate, manifest binding, build-v1.

Standard library only. This package owns the route-independent Part 1:
the section 1 plan gate and section 8 exit codes (gate.py), the
build-v1 manifest writer and validator (manifest.py), and the section 5
contents checks that need no image (checks.py). Route packaging is
Part 2.
"""
from __future__ import annotations

__all__ = ["checks", "gate", "manifest"]
