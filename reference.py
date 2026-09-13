#!/usr/bin/env python3
"""Executable reference implementation of the Open TCG Trade Protocol, v1.

This is the tie-breaker for the parts of SPEC.md where prose and code can drift: canonicalization
(4.5), the canonical byte encoding (5.5), the hash and bit derivation (5.2), and the filter itself.
Where this file and SPEC.md disagree, that is a bug in one of them and neither wins by default —
but `vectors/` is generated from this file, so an implementation that matches the vectors matches
this.

Deliberately dependency-free and game-agnostic: nothing here knows what Star Wars: Unlimited is.
Everything game-specific arrives through a definition file.

Not a transport implementation. Nothing here touches BLE; sections 3, 6 and 7 of the spec are out
of scope for this file by design.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

# --- protocol constants (SPEC.md 5.2, 5.3, 5.4) ------------------------------------------------

FILTER_BITS = 128       # m
HASH_COUNT = 4          # k
MAX_WANT_ENTRIES = 24   # nMax
MAX_BITS_SET = 96       # hostile-filter ceiling

FNV64_OFFSET = 14695981039346656037
FNV64_PRIME = 1099511628211
FNV64_MASK = (1 << 64) - 1


class NotCanonical(ValueError):
    """An identity that must be rejected rather than repaired (SPEC.md 4.5)."""


# --- definition file ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Definition:
    doc: dict

    @classmethod
    def load(cls, path) -> "Definition":
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh))

    @property
    def game(self) -> str:
        return self.doc["game"]

    @property
    def fields(self) -> list[dict]:
        return self.doc["identifier"]["fields"]

    @property
    def finishes(self) -> set[str]:
        return set(self.doc.get("finishes", []))

    def runs_for(self, code: str) -> list[dict]:
        return [s for s in self.doc.get("sets", []) if s["code"] == code]

    def code_is_shared(self, code: str) -> bool:
        """True when one printed set code covers more than one print run (SPEC.md 4.4.2)."""
        return len(self.runs_for(code)) > 1


# --- canonicalization (SPEC.md 4.5) ------------------------------------------------------------

def canonicalize(identity: dict, d: Definition) -> dict:
    """Return the canonical form, or raise NotCanonical.

    Normalizes case (rule 2) because that is a lossless rewriting of a value that is already
    correct. Every other rule REJECTS rather than repairs: a wrong printedTotal or a finish that
    contradicts the numbering is a different card, not a formatting variation, and silently
    "fixing" it would produce a filter that matches something the user does not hold.
    """
    declared = {f["name"]: f for f in d.fields}

    extra = set(identity) - set(declared)
    if extra:
        raise NotCanonical(f"undeclared field(s): {sorted(extra)}")           # rule 3

    out: dict = {}
    for name, f in declared.items():
        if name not in identity:
            if not f.get("optional"):
                raise NotCanonical(f"missing required field {name!r}")        # rule 1
            continue
        value = identity[name]
        if value is None:
            raise NotCanonical(f"field {name!r} is null; omit it instead")    # rule 1
        if f["type"] == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise NotCanonical(f"field {name!r} must be an integer, got {value!r}")
            out[name] = value
        else:
            if not isinstance(value, str):
                raise NotCanonical(f"field {name!r} must be a string, got {value!r}")
            case = f.get("case", "asIs")
            out[name] = value.upper() if case == "upper" else \
                        value.lower() if case == "lower" else value          # rule 2

    code = out.get("setCode")
    runs = d.runs_for(code)
    if not runs:
        raise NotCanonical(f"setCode {code!r} matches no entry in sets")       # rule 4

    shared = len(runs) > 1
    if shared:
        if "printedTotal" not in out:
            raise NotCanonical(
                f"setCode {code!r} is shared by {len(runs)} print runs, so printedTotal is required")
        matching = [r for r in runs if r.get("printedTotal") == out["printedTotal"]]
        if not matching:
            raise NotCanonical(
                f"printedTotal {out['printedTotal']!r} matches no print run under {code!r}")
        run = matching[0]
    else:
        if "printedTotal" in out:
            raise NotCanonical(
                f"setCode {code!r} belongs to one print run, so printedTotal must be absent")
        run = runs[0]                                                         # rule 5

    if "finish" in out and out["finish"] not in d.finishes:
        raise NotCanonical(f"finish {out['finish']!r} is not in this game's closed vocabulary")

    # Rule 7: variantKey is present if and ONLY if a registered collision matches. The "only if"
    # half matters as much as the other: a spurious variantKey changes the canonical bytes, so the
    # same physical card encoded with and without one lands on a disjoint set of filter bits and
    # the two never match — with nothing reporting an error.
    matched = [c for c in d.doc.get("knownCollisions", [])
               if c.get("matchFields")
               and all(out.get(k) == v for k, v in c["matchFields"].items())]
    if matched:
        allowed = {v["variantKey"] for v in matched[0].get("variants", [])}
        if "variantKey" not in out:
            raise NotCanonical(
                f"identity matches a registered collision, so variantKey is required; "
                f"expected one of {sorted(allowed)}")
        if out["variantKey"] not in allowed:
            raise NotCanonical(
                f"variantKey {out['variantKey']!r} is not registered for this collision; "
                f"expected one of {sorted(allowed)}")
    elif "variantKey" in out:
        raise NotCanonical(
            "variantKey is present but no registered collision matches this identity; it is for "
            "registered exceptions only (SPEC.md 4.6)")

    # Rule 6's catalog check cannot be made here: this file ships no card catalog, so it can only
    # enforce that the run declares a numbering rule at all. An implementation that DOES hold a
    # catalog must additionally verify, under "distinct", that the collectorNumber really is the
    # finish it claims. See SPEC.md 4.4.1 for why deriving a foil by flipping the field is wrong.
    if run.get("finishNumbering") not in ("shared", "distinct"):
        raise NotCanonical(f"print run for {code!r} declares no valid finishNumbering")

    return {name: out[name] for name in declared if name in out}


# --- canonical byte encoding (SPEC.md 5.5) -----------------------------------------------------

def canonical_bytes(identity: dict, d: Definition) -> bytes:
    """Encode an ALREADY-CANONICAL identity. Callers must canonicalize first."""
    buf = bytearray()
    for f in d.fields:
        name = f["name"]
        if name not in identity:
            buf.append(0x00)
            continue
        buf.append(0x01)
        value = identity[name]
        if f["type"] == "integer":
            buf.extend(int(value).to_bytes(4, "big", signed=False))
        else:
            buf.extend(value.encode("utf-8"))
    return bytes(buf)


# --- hashing and bit derivation (SPEC.md 5.2) --------------------------------------------------

def fnv1a64(data: bytes) -> int:
    h = FNV64_OFFSET
    for byte in data:
        h ^= byte
        h = (h * FNV64_PRIME) & FNV64_MASK
    return h


def bit_indices(identity: dict, d: Definition) -> list[int]:
    h = fnv1a64(canonical_bytes(identity, d))
    h1, h2 = h >> 32, h & 0xFFFFFFFF
    return [(h1 + i * h2) % FILTER_BITS for i in range(HASH_COUNT)]


# --- the filter (SPEC.md 5) --------------------------------------------------------------------

class BloomFilter:
    """A 128-bit filter, addressed big-endian: bit j is byte j//8, bit j%8 from the MSB."""

    def __init__(self, raw: bytes | None = None):
        if raw is not None and len(raw) != FILTER_BITS // 8:
            raise ValueError(f"filter must be {FILTER_BITS // 8} bytes, got {len(raw)}")
        self.bytes = bytearray(raw if raw is not None else FILTER_BITS // 8)

    def _set(self, j: int) -> None:
        self.bytes[j // 8] |= 0x80 >> (j % 8)

    def _get(self, j: int) -> bool:
        return bool(self.bytes[j // 8] & (0x80 >> (j % 8)))

    def add(self, identity: dict, d: Definition) -> None:
        for j in bit_indices(identity, d):
            self._set(j)

    def might_contain(self, identity: dict, d: Definition) -> bool:
        return all(self._get(j) for j in bit_indices(identity, d))

    @property
    def bits_set(self) -> int:
        return sum(bin(b).count("1") for b in self.bytes)

    def is_acceptable(self) -> bool:
        """SPEC.md 5.4 — a scanner must ignore a filter with more than MAX_BITS_SET bits set."""
        return self.bits_set <= MAX_BITS_SET

    def hex(self) -> str:
        return self.bytes.hex()

    @classmethod
    def build(cls, want: list[dict], d: Definition) -> "BloomFilter":
        """Build from a want list. Canonicalizes each entry and enforces nMax (SPEC.md 5.3)."""
        if len(want) > MAX_WANT_ENTRIES:
            raise ValueError(
                f"want list has {len(want)} entries, exceeding nMax={MAX_WANT_ENTRIES}; "
                f"truncate before building, do not overflow the filter")
        f = cls()
        for identity in want:
            f.add(canonicalize(identity, d), d)
        return f
