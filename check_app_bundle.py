#!/usr/bin/env python3
"""Assert the app's bundled copies match the canonical files in trade-protocol/.

The Xcode target uses a filesystem-synchronized group rooted at "SWU scan/", so a resource must
physically live there to be bundled — it cannot reference trade-protocol/ without editing the
project file. That leaves two copies of the definition and the vectors, and a silent divergence
between them is exactly the failure this protocol is most vulnerable to: the app would build
filters against one set of rules while claiming conformance to another.

So: copies, but never unchecked copies. Run this in CI alongside validate.py and
make_vectors.py --check.

    python3 trade-protocol/check_app_bundle.py
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAIRS = [
    (ROOT / "trade-protocol" / "definitions" / "swu.json",
     ROOT / "SWU scan" / "trade_definition_swu.json"),
    (ROOT / "trade-protocol" / "vectors" / "swu-v1.json",
     ROOT / "SWU scan" / "trade_vectors_swu_v1.json"),
]


def main() -> int:
    failed = 0
    for canonical, bundled in PAIRS:
        rel = bundled.relative_to(ROOT)
        if not bundled.exists():
            print(f"FAIL {rel} is missing; copy it from {canonical.relative_to(ROOT)}",
                  file=sys.stderr)
            failed += 1
        elif bundled.read_bytes() != canonical.read_bytes():
            print(f"FAIL {rel} has drifted from {canonical.relative_to(ROOT)}.\n"
                  f"     Copy the canonical file over it — never edit the bundled copy, which is "
                  f"downstream.", file=sys.stderr)
            failed += 1
        else:
            print(f"ok   {rel} matches {canonical.relative_to(ROOT)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
