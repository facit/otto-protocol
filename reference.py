#!/usr/bin/env python3
"""Executable reference implementation of the OTTO (Open TCG Trading Over-the-air), v1.

This is the tie-breaker for the parts of SPEC.md where prose and code can drift: canonicalization
(4.5), the canonical byte encoding (5.5), the hash and bit derivation (5.2), the filter itself, and
the optional trade negotiation feature (6.5): its message and receipt codecs, line ordering, hash
resolution, feature discovery and its state machine, written as a pure function.
Where this file and SPEC.md disagree, that is a bug in one of them and neither wins by default —
but `vectors/` is generated from this file, so an implementation that matches the vectors matches
this.

Deliberately dependency-free and game-agnostic: nothing here knows what Star Wars: Unlimited is.
Everything game-specific arrives through a definition file.

Not a transport implementation. Nothing here touches BLE; the connection handling of sections 3,
6 and 7 (and of 6.5.3) is out of scope for this file by design. What arrives over a connection is
modelled as plain events.
"""
from __future__ import annotations

import copy
import json
import uuid as _uuid
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


# ================================================================================================
# Trade negotiation (SPEC.md 6.5): optional feature "negotiation", format 1
# ================================================================================================
#
# Plain data throughout, so the vectors can carry it: a session id or negotiation id is a
# lowercase UUID string, a card hash is 16 lowercase hex digits, a message is a dict.
#
#   line     {"hash": "<16 hex>", "finish": int, "quantity": int, "condition": int}
#   SUGGEST  {"format": 1, "type": "SUGGEST", "negotiationId", "from", "to", "revision",
#             "give": [line, ...], "get": [line, ...]}     give/get from the SENDER's view
#   ACCEPT   {..., "type": "ACCEPT", "digest": "<16 hex>"}
#   DECLINE  {..., "type": "DECLINE", "reason": int}
#   CANCEL   {..., "type": "CANCEL"}

FEATURE_NEGOTIATION = "negotiation"
NEGOTIATION_FORMAT = 1           # the highest (and only) format this file implements
NEG_HEADER_BYTES = 52
NEG_LINE_BYTES = 11
NEG_MAX_LINES = 40
NEG_MAX_VALUE_BYTES = 512        # ATT attribute value limit
RECEIPT_BYTES = 21

MSG_TYPES = {0x01: "SUGGEST", 0x02: "ACCEPT", 0x03: "DECLINE", 0x04: "CANCEL"}
MSG_TYPE_CODES = {v: k for k, v in MSG_TYPES.items()}
NEG_BODY_BYTES = {"ACCEPT": 8, "DECLINE": 1, "CANCEL": 0}   # SUGGEST: 2 + 11 * lines

STATUSES = {
    0x00: "OK", 0x01: "DUPLICATE", 0x02: "SUPERSEDED", 0x03: "STALE", 0x04: "CLOSED",
    0x05: "NOT_FOR_ME", 0x06: "UNKNOWN_PEER", 0x07: "UNKNOWN_CARDS", 0x08: "BUSY",
    0x09: "RATE_LIMITED", 0x0A: "MALFORMED", 0x0B: "UNSUPPORTED_FORMAT",
}
STATUS_CODES = {v: k for k, v in STATUSES.items()}

#: Optional per-line card condition (SPEC.md 6.5.4). 0 = not stated; 8..255 reserved and read as 0.
CONDITIONS = {0: "notStated", 1: "mint", 2: "nearMint", 3: "excellent", 4: "good",
              5: "lightPlayed", 6: "played", 7: "poor"}

#: Reference values (SPEC.md 6.5.8): SHOULD, not interoperability requirements.
IDLE_TIMEOUT_S = 600
RATE_WINDOW_S = 60
RATE_MAX_PER_WINDOW = 10
RATE_MIN_INTERVAL_S = 1

ATT_INVALID_LENGTH = "invalidAttributeValueLength"


class AttError(Exception):
    """A transport fault answered with an ATT error; no receipt is stored (SPEC.md 6.5.3)."""

    def __init__(self, code: str = ATT_INVALID_LENGTH):
        super().__init__(code)
        self.code = code


class Rejected(Exception):
    """A message refused with a receipt status other than OK/DUPLICATE."""

    def __init__(self, status: str, detail: int = 0, why: str = ""):
        super().__init__(f"{status}: {why}" if why else status)
        self.status, self.detail, self.why = status, detail, why


def _id_bytes(value: str) -> bytes:
    return _uuid.UUID(value).bytes


def _id_str(raw: bytes) -> str:
    return str(_uuid.UUID(bytes=bytes(raw)))


# --- feature discovery (SPEC.md 6.1, 6.5.2) ------------------------------------------------------

def peer_feature_version(info: dict, feature: str = FEATURE_NEGOTIATION) -> int | None:
    """The highest version of `feature` a peer's Info declares, or None for "not supported".

    A missing or non-object `features`, a missing key, or a value that is not a number with an
    integral value >= 1 all mean "not supported". Unknown feature names are ignored.
    """
    features = info.get("features") if isinstance(info, dict) else None
    if not isinstance(features, dict):
        return None
    v = features.get(feature)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if isinstance(v, float) and not v.is_integer():
        return None
    v = int(v)
    return v if v >= 1 else None


def negotiated_format(own_max: int, peer_info: dict) -> int | None:
    """format = min(own, peer); None when the peer does not declare the feature."""
    peer = peer_feature_version(peer_info)
    return None if peer is None else min(own_max, peer)


# --- lines -----------------------------------------------------------------------------------

def finish_index(identity: dict, d: Definition) -> int:
    """The finish byte for a canonical identity: its finish's index in the definition's
    `finishes` array, or 0 for a game whose identifier declares no `finish` field."""
    if "finish" not in identity:
        return 0
    return d.doc["finishes"].index(identity["finish"])


def card_hash(identity: dict, d: Definition) -> str:
    """The 8-byte wire hash of SPEC.md 6.2, as 16 hex digits. Canonicalizes first."""
    return f"{fnv1a64(canonical_bytes(canonicalize(identity, d), d)):016x}"


def make_line(identity: dict, d: Definition, quantity: int, condition: int = 0) -> dict:
    canon = canonicalize(identity, d)
    return {"hash": card_hash(canon, d), "finish": finish_index(canon, d),
            "quantity": quantity, "condition": condition}


def line_key(line: dict) -> tuple[int, int]:
    """Line identity for ordering and merging: (hash, condition)."""
    return int(line["hash"], 16), line["condition"]


def normalize_lines(lines: list[dict]) -> list[dict]:
    """Merge lines with the same (hash, condition) by adding quantities, then sort ascending.

    For a sender whose own lines carry more than the wire does (a binder, a note): those lines
    collapse here. Raises ValueError if a merged quantity exceeds 255 or two lines with one hash
    disagree on finish (the hash alone decides the card, so they cannot both be right).
    """
    merged: dict[tuple[int, int], dict] = {}
    finish_of: dict[int, int] = {}
    for line in lines:
        key = line_key(line)
        if finish_of.setdefault(key[0], line["finish"]) != line["finish"]:
            raise ValueError(f"hash {line['hash']} appears with two different finish bytes")
        if key in merged:
            merged[key]["quantity"] += line["quantity"]
        else:
            merged[key] = {"hash": line["hash"].lower(), "finish": line["finish"],
                           "quantity": line["quantity"], "condition": line["condition"]}
    out = [merged[k] for k in sorted(merged)]
    for line in out:
        if not 1 <= line["quantity"] <= 255:
            raise ValueError(f"quantity {line['quantity']} outside 1..255 for {line['hash']}")
    return out


def mirror(proposal: dict) -> dict:
    """The receiver's view of a SUGGEST: the sender's give is the receiver's get, and vice versa."""
    return {"give": [dict(x) for x in proposal["get"]], "get": [dict(x) for x in proposal["give"]]}


def condition_for_display(condition: int) -> int:
    """Reserved condition values (8..255) are read as 0, "not stated"."""
    return condition if condition in CONDITIONS else 0


def wins_tie(a_id: str, b_id: str) -> bool:
    """Rule 2: of two session ids, the numerically smaller (16 bytes, unsigned) wins."""
    return _id_bytes(a_id) < _id_bytes(b_id)


def suggest_digest(body: bytes) -> str:
    """FNV-1a 64 of a SUGGEST's body bytes (offset 52 to the end) exactly as sent."""
    return f"{fnv1a64(body):016x}"


# --- message codec (SPEC.md 6.5.4) -------------------------------------------------------------

def pack_message(msg: dict) -> bytes:
    """Serialize WITHOUT validation. Only for building deliberately broken test inputs."""
    buf = bytearray()
    buf.append(msg.get("format", NEGOTIATION_FORMAT) & 0xFF)
    t = msg["type"]
    buf.append(MSG_TYPE_CODES[t] if isinstance(t, str) else t)
    for key in ("negotiationId", "from", "to"):
        buf += _id_bytes(msg[key])
    buf += int(msg["revision"]).to_bytes(2, "big")
    if t == "SUGGEST":
        give, get = msg["give"], msg["get"]
        buf.append(len(give) & 0xFF)
        buf.append(len(get) & 0xFF)
        for line in give + get:
            buf += bytes.fromhex(line["hash"])
            buf.append(line["finish"] & 0xFF)
            buf.append(line["quantity"] & 0xFF)
            buf.append(line["condition"] & 0xFF)
    elif t == "ACCEPT":
        buf += bytes.fromhex(msg["digest"])
    elif t == "DECLINE":
        buf.append(msg.get("reason", 0) & 0xFF)
    return bytes(buf)


def encode_message(msg: dict, d: Definition | None = None) -> bytes:
    """Encode a message, refusing anything a receiver would reject as MALFORMED."""
    data = pack_message(msg)
    if msg.get("format", NEGOTIATION_FORMAT) != NEGOTIATION_FORMAT:
        raise ValueError("this implementation encodes format 1 only")
    for key in ("give", "get"):
        for line in msg.get(key, []):
            for field in ("finish", "quantity", "condition"):
                if not 0 <= line[field] <= 255:
                    raise ValueError(f"{field} {line[field]} does not fit one byte")
    decode_message(data, d)   # the same checks a receiver makes
    return data


def _check_side(lines: list[dict], side: str) -> None:
    prev = None
    for line in lines:
        if line["quantity"] == 0:
            raise Rejected("MALFORMED", why=f"{side}: quantity 0")
        key = line_key(line)
        if prev is not None and key <= prev:
            raise Rejected("MALFORMED", why=f"{side}: lines not strictly ascending by "
                                             f"(hash, condition)")
        prev = key


def decode_message(data: bytes, d: Definition | None = None) -> dict:
    """Decode and structurally validate one value written to Negotiation.

    Checks in this order (SPEC.md 6.5.4, 6.5.6): value length (ATT error), format, header length
    (ATT error), type, revision, exact length, line count, quantity, line order, and, given a
    definition, the finish byte's range. Raises AttError or Rejected. Hash resolution is separate
    (resolve_lines), because a receiver checks the sender first (SPEC.md 6.5.6).
    """
    data = bytes(data)
    if len(data) == 0 or len(data) > NEG_MAX_VALUE_BYTES:
        raise AttError()
    if data[0] != NEGOTIATION_FORMAT:
        if data[0] > NEGOTIATION_FORMAT:
            raise Rejected("UNSUPPORTED_FORMAT", NEGOTIATION_FORMAT, f"format {data[0]}")
        raise Rejected("MALFORMED", why="format 0")
    if len(data) < NEG_HEADER_BYTES:
        raise AttError()
    type_code = data[1]
    if type_code not in MSG_TYPES:
        raise Rejected("MALFORMED", why=f"unknown type {type_code}")
    t = MSG_TYPES[type_code]
    revision = int.from_bytes(data[50:52], "big")
    if revision == 0:
        raise Rejected("MALFORMED", why="revision 0")
    msg = {"format": data[0], "type": t, "negotiationId": _id_str(data[2:18]),
           "from": _id_str(data[18:34]), "to": _id_str(data[34:50]), "revision": revision}
    body = data[NEG_HEADER_BYTES:]

    if t != "SUGGEST":
        if len(body) != NEG_BODY_BYTES[t]:
            raise Rejected("MALFORMED", why=f"{t} must be {NEG_HEADER_BYTES + NEG_BODY_BYTES[t]} "
                                            f"bytes, got {len(data)}")
        if t == "ACCEPT":
            msg["digest"] = body.hex()
        elif t == "DECLINE":
            msg["reason"] = body[0]
        return msg

    if len(body) < 2:
        raise Rejected("MALFORMED", why="SUGGEST without counts")
    give_n, get_n = body[0], body[1]
    n = give_n + get_n
    if not 1 <= n <= NEG_MAX_LINES:
        raise Rejected("MALFORMED", why=f"{n} lines, must be 1..{NEG_MAX_LINES}")
    if len(body) != 2 + NEG_LINE_BYTES * n:
        raise Rejected("MALFORMED", why=f"length {len(data)} != "
                                        f"{NEG_HEADER_BYTES + 2 + NEG_LINE_BYTES * n}")
    lines = []
    for i in range(n):
        raw = body[2 + NEG_LINE_BYTES * i: 2 + NEG_LINE_BYTES * (i + 1)]
        lines.append({"hash": raw[:8].hex(), "finish": raw[8], "quantity": raw[9],
                      "condition": raw[10]})
    msg["give"], msg["get"] = lines[:give_n], lines[give_n:]
    _check_side(msg["give"], "give")
    _check_side(msg["get"], "get")
    if d is not None:
        has_finish = any(f["name"] == "finish" for f in d.fields)
        limit = len(d.doc.get("finishes", [])) if has_finish else 1
        for line in lines:
            if line["finish"] >= limit:
                raise Rejected("MALFORMED", why=f"finish byte {line['finish']} out of range")
    return msg


# --- hash resolution (SPEC.md 6.5.5) -----------------------------------------------------------

def build_reverse_table(identities: list[dict], d: Definition) -> dict[str, dict]:
    """hash -> canonical identity over every identity the receiver can display.

    Raises ValueError on a collision (two distinct identities, one hash), which SPEC says an
    implementation SHOULD assert never happens.
    """
    table: dict[str, dict] = {}
    for ident in identities:
        canon = canonicalize(ident, d)
        h = card_hash(canon, d)
        if h in table and table[h] != canon:
            raise ValueError(f"hash collision on {h}: {table[h]!r} and {canon!r}")
        table[h] = canon
    return table


def resolve_lines(msg: dict, table: dict[str, dict], d: Definition) -> dict:
    """Resolve a decoded SUGGEST's hashes. Returns {"give": [...], "get": [...]} with each line's
    "identity" added; raises Rejected UNKNOWN_CARDS (detail = distinct unresolvable hashes) or,
    once every hash resolved, MALFORMED for a finish byte that contradicts the identity."""
    lines = msg["give"] + msg["get"]
    unknown = {line["hash"] for line in lines if line["hash"] not in table}
    if unknown:
        raise Rejected("UNKNOWN_CARDS", min(len(unknown), 255), f"{len(unknown)} unknown hashes")
    for line in lines:
        if finish_index(table[line["hash"]], d) != line["finish"]:
            raise Rejected("MALFORMED", why=f"finish byte {line['finish']} contradicts "
                                            f"{table[line['hash']]!r}")
    return {side: [{**line, "identity": table[line["hash"]]} for line in msg[side]]
            for side in ("give", "get")}


# --- receipts (SPEC.md 6.5.7) ------------------------------------------------------------------

def encode_receipt(status: str, negotiation_id: str, revision: int, detail: int = 0) -> bytes:
    return (bytes([NEGOTIATION_FORMAT, STATUS_CODES[status]]) + _id_bytes(negotiation_id)
            + revision.to_bytes(2, "big") + bytes([detail]))


def decode_receipt(data: bytes) -> dict:
    """Decode a receipt. Raises ValueError for a value that is not a format-1 receipt (the
    sender treats that as a failed delivery). An unknown status decodes with "status": None."""
    data = bytes(data)
    if len(data) != RECEIPT_BYTES:
        raise ValueError(f"receipt must be {RECEIPT_BYTES} bytes, got {len(data)}")
    if data[0] != NEGOTIATION_FORMAT:
        raise ValueError(f"receipt format {data[0]}")
    return {"format": data[0], "statusCode": data[1], "status": STATUSES.get(data[1]),
            "negotiationId": _id_str(data[2:18]), "revision": int.from_bytes(data[18:20], "big"),
            "detail": data[20]}


def receipt_delivered(receipt: dict) -> bool:
    """Only OK and DUPLICATE mean the message was applied. Unknown statuses are failures."""
    return receipt["status"] in ("OK", "DUPLICATE")


ZERO_ID = "00000000-0000-0000-0000-000000000000"


def _echo(data: bytes) -> tuple[str, int]:
    """The id and revision a receipt echoes: from the header when it is a format-1 header,
    otherwise zero (the layout of an unknown format is not known)."""
    if len(data) >= NEG_HEADER_BYTES and data[0] == NEGOTIATION_FORMAT:
        return _id_str(data[2:18]), int.from_bytes(data[50:52], "big")
    return ZERO_ID, 0


# --- state machine (SPEC.md 6.5.8) -------------------------------------------------------------
#
# One device's negotiation state is a plain dict; step() is a pure function
#     step(config, state, event) -> (new_state, output)
# config = {"sessionId": str, "definition": Definition, "table": reverse table}.
# This reference holds at most one live negotiation per device (SPEC allows a limit; at the
# limit a new SUGGEST from another peer is BUSY).

LIVE = {"SENT", "RECEIVED", "DECLINED_BY_ME", "DECLINED_BY_PEER", "ACCEPTING"}
OPEN = LIVE - {"ACCEPTING"}
TERMINAL = {"ACCEPTED", "CANCELLED", "EXPIRED"}


def initial_state(session_id: str, matches: list[str]) -> dict:
    return {"me": session_id, "matches": list(matches), "negotiation": None, "closed": [],
            "recent": {}}


def snapshot(state: dict) -> dict:
    """The comparable part of a state, as the scenario vectors record it."""
    n = state["negotiation"]
    if n is None:
        return {"state": "IDLE"}
    cur = n["current"]
    return {"state": n["state"], "negotiationId": n["id"], "peer": n["peer"],
            "revision": n["revision"], "currentAuthor": cur["author"] if cur else None,
            "currentDigest": cur["digest"] if cur else None, "delivery": n["delivery"]}


def _terminate(state: dict, n: dict, to: str) -> None:
    n["state"] = to
    if n["id"] not in state["closed"]:
        state["closed"].append(n["id"])


def _send(n: dict, msg: dict, d: Definition, now: int) -> dict:
    data = encode_message(msg, d)
    n["outbox"], n["delivery"], n["lastActivity"] = data.hex(), "sending", now
    return {"send": data.hex()}


def _cancel(state: dict, n: dict, d: Definition, now: int, to: str) -> dict:
    _terminate(state, n, to)
    return _send(n, {"type": "CANCEL", "negotiationId": n["id"], "from": state["me"],
                     "to": n["peer"], "revision": n["revision"]}, d, now)


def step(config: dict, state: dict, event: dict) -> tuple[dict, dict]:
    state = copy.deepcopy(state)
    handler = {"suggest": _local_suggest, "accept": _local_accept, "decline": _local_decline,
               "end": _local_end, "stop": _local_end, "retry": _local_retry, "tick": _tick,
               "matchPruned": _match_pruned, "incoming": _incoming,
               "delivery": _delivery}[event["type"]]
    return state, handler(config, state, event)


def _refuse(why: str) -> dict:
    return {"refused": why}


def _busy_outbox(n: dict | None) -> bool:
    """One outbox per side: the next message waits for the previous one's receipt (or failure).
    Only a CANCEL (L4-L6) may replace a message still being delivered."""
    return n is not None and n["delivery"] == "sending"


def _local_suggest(config, state, ev):
    """L1: send a suggestion (new negotiation from IDLE or a terminal state, else next revision)."""
    d, n, now = config["definition"], state["negotiation"], ev["now"]
    lines = {"give": normalize_lines(ev.get("give", [])), "get": normalize_lines(ev.get("get", []))}
    if n is None or n["state"] in TERMINAL:
        if ev["peer"] not in state["matches"]:
            return _refuse("peer is not a current match")
        n = {"id": ev["negotiationId"], "peer": ev["peer"], "state": None, "revision": 0,
             "current": None, "lastActivity": now, "lastIncoming": None, "outbox": None,
             "delivery": None}
    elif n["peer"] != ev["peer"]:
        return _refuse("a negotiation with another peer is open")
    elif n["state"] not in OPEN:
        return _refuse(f"cannot suggest in {n['state']}")
    elif _busy_outbox(n):
        return _refuse("a delivery is in progress")
    if n["revision"] >= 0xFFFF:
        return _refuse("revision space exhausted")
    msg = {"type": "SUGGEST", "negotiationId": n["id"], "from": state["me"], "to": n["peer"],
           "revision": n["revision"] + 1, **lines}
    data = encode_message(msg, d)                       # Rule 1: commit, then deliver
    n.update(state="SENT", revision=msg["revision"],
             current={"revision": msg["revision"], "author": "me",
                      "body": data[NEG_HEADER_BYTES:].hex(),
                      "digest": suggest_digest(data[NEG_HEADER_BYTES:])})
    state["negotiation"] = n
    return _send(n, msg, d, now)


def _local_accept(config, state, ev):
    """L2: accept the peer's current suggestion."""
    n = state["negotiation"]
    if n is None or n["state"] != "RECEIVED":
        return _refuse("nothing to accept")
    if _busy_outbox(n):
        return _refuse("a delivery is in progress")
    n["state"] = "ACCEPTING"
    return _send(n, {"type": "ACCEPT", "negotiationId": n["id"], "from": state["me"],
                     "to": n["peer"], "revision": n["current"]["revision"],
                     "digest": n["current"]["digest"]}, config["definition"], ev["now"])


def _local_decline(config, state, ev):
    """L3: decline the peer's current suggestion."""
    n = state["negotiation"]
    if n is None or n["state"] != "RECEIVED":
        return _refuse("nothing to decline")
    if _busy_outbox(n):
        return _refuse("a delivery is in progress")
    n["state"] = "DECLINED_BY_ME"
    return _send(n, {"type": "DECLINE", "negotiationId": n["id"], "from": state["me"],
                     "to": n["peer"], "revision": n["current"]["revision"],
                     "reason": ev.get("reason", 0)}, config["definition"], ev["now"])


def _local_end(config, state, ev):
    """L4 (end trade) and L5 (trading session stopped): cancel, best effort."""
    n = state["negotiation"]
    if n is None or n["state"] not in LIVE:
        return _refuse("no live negotiation") if ev["type"] == "end" else {}
    return _cancel(state, n, config["definition"], ev["now"], "CANCELLED")


def _local_retry(config, state, ev):
    """Deliver the outbox again, byte for byte (after a failed delivery or a lost receipt)."""
    n = state["negotiation"]
    if n is None or n["outbox"] is None:
        return _refuse("nothing to retry")
    n["delivery"] = "sending"
    return {"send": n["outbox"]}


def _tick(config, state, ev):
    """L6: idle timeout."""
    n = state["negotiation"]
    if n is not None and n["state"] in LIVE and ev["now"] - n["lastActivity"] >= IDLE_TIMEOUT_S:
        return _cancel(state, n, config["definition"], ev["now"], "EXPIRED")
    return {}


def _match_pruned(config, state, ev):
    """L6: the peer's match was pruned."""
    if ev["peer"] in state["matches"]:
        state["matches"].remove(ev["peer"])
    n = state["negotiation"]
    if n is not None and n["state"] in LIVE and n["peer"] == ev["peer"]:
        return _cancel(state, n, config["definition"], ev["now"], "EXPIRED")
    return {}


def _delivery(config, state, ev):
    """The outcome of delivering the outbox: a receipt read back, or a local failure
    ("UNREACHABLE": peer not found or connection failed; "UNSUPPORTED": no Negotiation
    characteristic)."""
    n = state["negotiation"]
    if n is None or n["outbox"] is None:
        return {}
    out = bytes.fromhex(n["outbox"])
    sent_type = MSG_TYPES[out[1]]
    if "failure" in ev:
        n["delivery"] = ev["failure"]
        return {}
    try:
        rc = decode_receipt(bytes.fromhex(ev["receipt"]))
    except ValueError:
        n["delivery"] = "INVALID_RECEIPT"
        return {}
    if rc["status"] is None:
        n["delivery"] = "UNKNOWN_STATUS"
        return {}
    if rc["status"] != "UNSUPPORTED_FORMAT" and (
            rc["negotiationId"] != _id_str(out[2:18])
            or rc["revision"] != int.from_bytes(out[50:52], "big")):
        n["delivery"] = "INVALID_RECEIPT"
        return {}
    delivered = receipt_delivered(rc)
    n["delivery"] = "delivered" if delivered else rc["status"]
    if delivered:
        n["outbox"] = None
    if sent_type == "ACCEPT" and n["state"] == "ACCEPTING":
        if delivered:
            _terminate(state, n, "ACCEPTED")
        elif rc["status"] == "STALE":
            n["state"] = "RECEIVED"
        elif rc["status"] == "CLOSED":
            _terminate(state, n, "CANCELLED")
    return {}


def _receipt(status: str, data: bytes, detail: int = 0) -> dict:
    nid, rev = _echo(data)
    return {"receipt": encode_receipt(status, nid, rev, detail).hex(), "status": status}


def _incoming(config, state, ev):
    """A value written to our Negotiation characteristic, on a connection whose central wrote
    Info with id `connectionInfoId` (None if it wrote none)."""
    d, table, now = config["definition"], config["table"], ev["now"]
    data = bytes.fromhex(ev["data"])
    try:
        msg = decode_message(data, d)
    except AttError as e:
        return {"att": e.code}
    except Rejected as e:
        return _receipt(e.status, data, e.detail)

    if msg["to"] != state["me"]:
        return _receipt("NOT_FOR_ME", data)
    peer = msg["from"]
    if peer not in state["matches"] or peer != ev.get("connectionInfoId"):
        return _receipt("UNKNOWN_PEER", data)

    recent = [t for t in state["recent"].get(peer, []) if now - t < RATE_WINDOW_S]
    if (recent and now - recent[-1] < RATE_MIN_INTERVAL_S) or len(recent) >= RATE_MAX_PER_WINDOW:
        state["recent"][peer] = recent
        return _receipt("RATE_LIMITED", data)
    state["recent"][peer] = recent + [now]

    if msg["type"] == "SUGGEST":
        try:
            resolve_lines(msg, table, d)
        except Rejected as e:
            return _receipt(e.status, data, e.detail)

    n = state["negotiation"]
    mid, t, rev = msg["negotiationId"], msg["type"], msg["revision"]
    same = n is not None and n["id"] == mid and n["peer"] == peer

    if same and n["lastIncoming"] == data.hex():
        return _receipt("DUPLICATE", data)          # a retry after a lost receipt
    if mid in state["closed"]:
        return _receipt("CLOSED", data)

    def applied(next_state: str) -> dict:
        n["state"], n["lastIncoming"], n["lastActivity"] = next_state, data.hex(), now
        return _receipt("OK", data)

    def adopt() -> dict:
        nonlocal n
        body = data[NEG_HEADER_BYTES:]
        if n is None or n["id"] != mid:
            n = {"id": mid, "peer": peer, "outbox": None, "delivery": None}
            state["negotiation"] = n
        n.update(revision=rev, current={"revision": rev, "author": "peer", "body": body.hex(),
                                        "digest": suggest_digest(body)})
        return applied("RECEIVED")

    live = n is not None and n["state"] in LIVE

    if t == "SUGGEST":
        if not live:
            return adopt()
        if n["peer"] != peer:
            return _receipt("BUSY", data)
        cur = n["current"]
        if n["id"] == mid:
            if rev > n["revision"]:
                return adopt()
            if rev == n["revision"] and cur["author"] == "peer" \
                    and cur["digest"] == suggest_digest(data[NEG_HEADER_BYTES:]):
                return _receipt("DUPLICATE", data)
            if rev == n["revision"] and n["state"] == "SENT" and wins_tie(peer, state["me"]):
                return adopt()                       # crossing suggestions, theirs wins
            return _receipt("SUPERSEDED", data)
        # Another negotiation id with the same peer: both opened at once. Rule 2 on
        # (revision, author): the higher revision wins, then the smaller author session id.
        mine_author = state["me"] if cur["author"] == "me" else peer
        if rev != n["revision"]:
            theirs = rev > n["revision"]
        else:
            theirs = mine_author == peer or wins_tie(peer, mine_author)
        return adopt() if theirs else _receipt("SUPERSEDED", data)

    if not same or not live:
        return _receipt("CLOSED", data)
    if t == "CANCEL":
        n["lastIncoming"], n["lastActivity"] = data.hex(), now
        _terminate(state, n, "CANCELLED")
        return _receipt("OK", data)
    cur = n["current"]
    if n["state"] != "SENT" or cur["author"] != "me" or rev != cur["revision"]:
        return _receipt("STALE", data)
    if t == "ACCEPT":
        if msg["digest"] != cur["digest"]:
            return _receipt("STALE", data)
        out = applied("ACCEPTED")
        _terminate(state, n, "ACCEPTED")
        return out
    return applied("DECLINED_BY_PEER")              # DECLINE


class NegotiationEndpoint:
    """A stateful convenience wrapper around step(), for one device."""

    def __init__(self, session_id: str, matches: list[str], d: Definition,
                 table: dict[str, dict]):
        self.config = {"sessionId": session_id, "definition": d, "table": table}
        self.state = initial_state(session_id, matches)

    def apply(self, event: dict) -> dict:
        self.state, out = step(self.config, self.state, event)
        return out

    def apply_local(self, event: dict) -> dict:
        return self.apply(event)

    def apply_incoming(self, data: bytes, connection_info_id: str | None, now: int) -> dict:
        return self.apply({"type": "incoming", "data": bytes(data).hex(),
                           "connectionInfoId": connection_info_id, "now": now})
