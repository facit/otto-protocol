#!/usr/bin/env python3
"""App tooling: assert an app's bundled copies match the canonical files in this repository.

Not part of the protocol. An app that ships the definition file and the vectors as bundled
resources holds copies of them, and a silent divergence between a copy and the canonical file is
exactly the failure this protocol is most vulnerable to: the app would build filters against one
set of rules while claiming conformance to another. So: copies, but never unchecked copies. Run
this in the app's CI alongside validate.py and make_vectors.py --check.

    python3 check_app_bundle.py /path/to/app-repo

PAIRS below lists where one app (the iOS app this protocol was first built in) bundles its
copies, relative to the app repository's root. Another app edits PAIRS or copies this script.
"""
from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
#: (canonical file in this repository, bundled copy relative to the app repository root)
PAIRS = [
    ("definitions/swu.json", "SWU scan/trade_definition_swu.json"),
    ("vectors/swu-v1.json", "SWU scan/trade_vectors_swu_v1.json"),
]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {pathlib.Path(argv[0]).name} APP_REPO_PATH", file=sys.stderr)
        return 2
    app = pathlib.Path(argv[1]).resolve()
    if not app.is_dir():
        print(f"FAIL {app} is not a directory", file=sys.stderr)
        return 2
    failed = 0
    for canonical_rel, bundled_rel in PAIRS:
        canonical, bundled = HERE / canonical_rel, app / bundled_rel
        if not bundled.exists():
            print(f"FAIL {bundled_rel} is missing; copy it from {canonical_rel}", file=sys.stderr)
            failed += 1
        elif bundled.read_bytes() != canonical.read_bytes():
            print(f"FAIL {bundled_rel} has drifted from {canonical_rel}.\n"
                  f"     Copy the canonical file over it — never edit the bundled copy, which is "
                  f"downstream.", file=sys.stderr)
            failed += 1
        else:
            print(f"ok   {bundled_rel} matches {canonical_rel}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
