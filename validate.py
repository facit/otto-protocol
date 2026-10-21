#!/usr/bin/env python3
"""Contribution-time validation for OTTO (Open TCG Trading Over-the-air) definition files.

Checks the rules SPEC.md 4.6 lists as ones the JSON Schema structurally cannot enforce on its own,
plus the canonicalization invariants of SPEC.md 4.4.2/4.5 that the same argument applies to. Run
this on every pull request that touches definitions/, alongside ordinary schema validation.

Schema validation itself is deliberately NOT reimplemented here: use a real JSON Schema validator
(`pip install jsonschema`), which this script will use if it is importable and otherwise skip with
a warning rather than pretending to have checked.

SPEC.md 4.6 rule 6 compares a file against its own previous revision. Given that revision with
--base, this script checks the part of it that is a pure list comparison: `finishes` is
append-only (every earlier value keeps its position, because the negotiation feature sends a
finish as its index, SPEC.md 6.5.4), and definitionVersion increased if anything changed.
NOT CHECKED even then: that no already-published printedTotal or finishNumbering changed value.
Matching print runs across revisions needs judgement, so that stays with the CI job (or reviewer)
that knows the base revision; do not read a green run as covering it.

    python3 validate.py                                   # every file in definitions/
    python3 validate.py path/to/x.json                    # just this one
    python3 validate.py --base old/x.json path/to/x.json  # also rule 6 against the old revision

Exits non-zero if any file fails.
"""
from __future__ import annotations

import json
import pathlib
import sys
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parent
SCHEMA = ROOT / "schema" / "game-definition.schema.json"


def check(doc: dict, name: str) -> list[str]:
    """Return a list of human-readable problems; empty means the file is valid."""
    bad: list[str] = []
    fail = bad.append

    fields = {f["name"]: f for f in doc.get("identifier", {}).get("fields", [])}
    sets = doc.get("sets", [])
    finishes = set(doc.get("finishes", []))

    # --- SPEC.md 4.4.2 / 4.6 rules 4-5: printedTotal is a disambiguator for shared codes ---
    by_code: dict[str, list[dict]] = defaultdict(list)
    for entry in sets:
        by_code[entry.get("code")].append(entry)

    if "printedTotal" in fields and not fields["printedTotal"].get("optional"):
        fail("identifier field 'printedTotal' must be optional — it is absent on unshared codes")

    for code, entries in sorted(by_code.items()):
        has = [e for e in entries if "printedTotal" in e]
        if len(entries) > 1 and len(has) != len(entries):
            fail(f"set code {code!r}: shared by {len(entries)} print runs but only {len(has)} "
                 f"declare printedTotal — every entry on a shared code needs one (rule 4)")
        if len(entries) == 1 and has:
            fail(f"set code {code!r}: belongs to one print run, so it must not declare "
                 f"printedTotal (rule 4 / SPEC.md 4.4.2)")
        totals = Counter(e.get("printedTotal") for e in entries)
        for total, n in totals.items():
            if n > 1:
                fail(f"set code {code!r}: {n} entries share printedTotal {total!r} — nothing "
                     f"distinguishes them (rule 5)")

    # --- SPEC.md 4.4.1: finishNumbering must be declared and meaningful ---
    for entry in sets:
        fn = entry.get("finishNumbering")
        if fn not in ("shared", "distinct"):
            fail(f"set {entry.get('code')!r} ({entry.get('name')!r}): finishNumbering must be "
                 f"'shared' or 'distinct', got {fn!r}")

    # --- SPEC.md 4.6 rules 1-3: knownCollisions integrity ---
    seen_matches: list[dict] = []
    for i, coll in enumerate(doc.get("knownCollisions", [])):
        match = coll.get("matchFields", {})
        for key in match:
            if key == "variantKey":
                fail(f"knownCollisions[{i}]: matchFields must not key on variantKey itself (rule 1)")
            elif key not in fields:
                fail(f"knownCollisions[{i}]: matchFields key {key!r} is not a declared "
                     f"identifier field (rule 1)")
        keys = [v.get("variantKey") for v in coll.get("variants", [])]
        dupes = [k for k, n in Counter(keys).items() if n > 1]
        if dupes:
            fail(f"knownCollisions[{i}]: duplicate variantKey(s) {dupes} within one entry (rule 2)")
        if match in seen_matches:
            fail(f"knownCollisions[{i}]: matchFields duplicates an earlier entry (rule 3)")
        seen_matches.append(match)
    if doc.get("knownCollisions") and "variantKey" not in fields:
        fail("knownCollisions entries exist but 'variantKey' is not a declared identifier field")

    # --- consistency checks the schema cannot express ---
    if "finish" in fields and not finishes:
        fail("identifier includes a 'finish' field but 'finishes' declares no vocabulary")
    for f in doc.get("identifier", {}).get("fields", []):
        if f.get("type") == "string" and "case" not in f:
            fail(f"identifier field {f['name']!r} is a string but declares no 'case' rule — "
                 f"normalization would be implementation-defined (SPEC.md 4.5 rule 2)")
        if f.get("type") == "integer" and "case" in f:
            fail(f"identifier field {f['name']!r} is an integer and must not declare 'case'")

    return bad


def check_against_base(doc: dict, base: dict) -> list[str]:
    """SPEC.md 4.6 rule 6, the parts that need no judgement: finishes append-only, and
    definitionVersion increased when the file changed."""
    bad: list[str] = []
    old, new = base.get("finishes", []), doc.get("finishes", [])
    if new[:len(old)] != old:
        bad.append(f"finishes is append-only (rule 6): the previous revision's {old!r} must be "
                   f"a prefix of {new!r}; a finish is sent as its index (SPEC.md 6.5.4)")
    if doc != base and not doc.get("definitionVersion", 0) > base.get("definitionVersion", 0):
        bad.append(f"definitionVersion must increase when the file changes (rule 6): "
                   f"{base.get('definitionVersion')!r} -> {doc.get('definitionVersion')!r}")
    return bad


def validate_schema(doc: dict, name: str) -> list[str]:
    try:
        import jsonschema
    except ImportError:
        print(f"  ! jsonschema not installed — schema validation SKIPPED for {name}, "
              f"rule checks still ran", file=sys.stderr)
        return []
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    return [f"schema: {e.json_path}: {e.message}" for e in validator.iter_errors(doc)]


def main(argv: list[str]) -> int:
    args = argv[1:]
    base = None
    if args and args[0] == "--base":
        if len(args) != 3:
            print("usage: validate.py --base OLD.json NEW.json", file=sys.stderr)
            return 2
        base = json.loads(pathlib.Path(args[1]).read_text(encoding="utf-8"))
        args = args[2:]
    paths = [pathlib.Path(a) for a in args] or sorted((ROOT / "definitions").glob("*.json"))
    if not paths:
        print("no definition files found", file=sys.stderr)
        return 1

    failed = 0
    for path in paths:
        doc = json.loads(path.read_text(encoding="utf-8"))
        problems = validate_schema(doc, path.name) + check(doc, path.name)
        if base is not None:
            problems += check_against_base(doc, base)
        if problems:
            failed += 1
            print(f"FAIL {path}")
            for p in problems:
                print(f"  - {p}")
        else:
            n_runs = len(doc.get("sets", []))
            unverified = sum(1 for e in doc.get("sets", []) if e.get("verified") is False)
            note = f", {unverified} unverified" if unverified else ""
            print(f"ok   {path}  ({n_runs} print runs{note})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
