#!/usr/bin/env python3
"""Generate conformance test vectors from reference.py.

Run from anywhere:  python3 make_vectors.py [--check]

The output (vectors/swu-v1.json) is committed. Regenerate it only when the protocol's encoding or
parameters change, and treat any unexplained diff in the regenerated file as a protocol break, not
a refresh — the whole point of the vectors is that these bytes do not move.

Real card identities come from testdata/swu-catalog-sample.json, a vendored sample of a real
catalog, so the false-positive vector exercises the hash against genuine data rather than
synthetic strings. Nothing outside this repository is read.
"""
from __future__ import annotations

import json
import pathlib
import sys
import uuid

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import reference as r  # noqa: E402

ROOT = HERE
DEFN = HERE / "definitions" / "swu.json"
SAMPLE = HERE / "testdata" / "swu-catalog-sample.json"

d = r.Definition.load(DEFN)

#: Fixed forever; see SPEC.md 3.1.
PROTOCOL_NAMESPACE = uuid.UUID("8a44bf6a-7c5c-40fa-84d1-f3a5ec791831")


def catalog() -> tuple[list[dict], dict]:
    """The vendored sample of real card identities, and the known-gap counts recorded with it.

    The sample lives in this repository (testdata/), so the vectors regenerate from the repository
    alone. Every entry must already be canonical: a sample entry that is not is a broken test
    fixture, not a card to skip.
    """
    doc = json.loads(SAMPLE.read_text(encoding="utf-8"))
    assert doc["game"] == d.game, f"{SAMPLE.name} is for {doc['game']!r}, not {d.game!r}"
    out = []
    for ident in doc["identities"]:
        canon = r.canonicalize(ident, d)
        assert canon == ident, f"{SAMPLE.name}: {ident!r} is not canonical"
        out.append(canon)
    gaps = doc["knownGaps"]
    return out, {"non_numeric": gaps["tokensOutOfScope"], "unknown_finish": gaps["unknownFinish"]}


def encoding_vector(identity: dict, note: str) -> dict:
    canon = r.canonicalize(identity, d)
    raw = r.canonical_bytes(canon, d)
    return {
        "note": note,
        "input": identity,
        "canonical": canon,
        "canonicalBytesHex": raw.hex(),
        "fnv1a64Hex": f"{r.fnv1a64(raw):016x}",
        "bitIndices": r.bit_indices(canon, d),
    }


def rejection_vector(identity: dict, note: str) -> dict:
    try:
        r.canonicalize(identity, d)
    except r.NotCanonical as exc:
        return {"note": note, "input": identity, "reason": str(exc)}
    raise AssertionError(f"expected {identity!r} to be rejected ({note})")


def selftest_variant_key() -> None:
    """Exercise both halves of rule 7.

    swu.json's knownCollisions is empty, so the "required when a collision matches" half cannot be
    reached from the real definition file and would otherwise go untested until the day someone
    registers a real collision — which is the worst time to discover it is broken. A synthetic
    definition covers it here.
    """
    synthetic = r.Definition({
        "game": "test", "definitionVersion": 1,
        "identifier": {"fields": [
            {"name": "setCode", "type": "string", "case": "upper"},
            {"name": "collectorNumber", "type": "integer"},
            {"name": "finish", "type": "string", "case": "lower"},
            {"name": "variantKey", "type": "string", "case": "lower", "optional": True},
        ]},
        "finishes": ["standard"],
        "sets": [{"code": "AAA", "name": "Test", "finishNumbering": "shared"}],
        "knownCollisions": [{
            "matchFields": {"setCode": "AAA", "collectorNumber": 1},
            "variants": [{"variantKey": "left", "description": "has a left mark"},
                         {"variantKey": "right", "description": "has a right mark"}],
        }],
    })
    base = {"setCode": "AAA", "collectorNumber": 1, "finish": "standard"}

    for bad, why in [(base, "collision matches but variantKey absent"),
                     ({**base, "variantKey": "middle"}, "variantKey not registered for it")]:
        try:
            r.canonicalize(bad, synthetic)
        except r.NotCanonical:
            pass
        else:
            raise AssertionError(f"rule 7 not enforced: {why}")

    for good in ({**base, "variantKey": "left"}, {**base, "variantKey": "right"}):
        r.canonicalize(good, synthetic)

    # a card the collision does not match must still refuse a variantKey
    try:
        r.canonicalize({"setCode": "AAA", "collectorNumber": 2, "finish": "standard",
                        "variantKey": "left"}, synthetic)
    except r.NotCanonical:
        pass
    else:
        raise AssertionError("rule 7 not enforced: variantKey on a non-colliding card")
    print("selftest: rule 7 enforced in both directions")


# --- negotiation vectors (SPEC.md 6.5) -----------------------------------------------------------

#: Fixed session and negotiation ids. A < B < C as 16-byte unsigned numbers (Rule 2).
SID_A = "3f2b8c1e-5a4d-4e0b-9c7a-1d2e3f4a5b6c"
SID_B = "9a7e4d21-0c3b-4f8a-b6d5-2e1f0a9b8c7d"
SID_C = "c5d4e3f2-a1b0-4c9d-8e7f-6a5b4c3d2e1f"
NID_1 = "6f1c2d3e-4b5a-4c6d-9e8f-0a1b2c3d4e5f"
NID_2 = "d2e3f4a5-b6c7-4d8e-a9f0-1b2c3d4e5f60"
NID_3 = "2b3c4d5e-6f70-4182-93a4-b5c6d7e8f901"


def ident(code: str, number: int, finish: str, total: int | None = None) -> dict:
    out = {"setCode": code, "collectorNumber": number, "finish": finish}
    if total is not None:
        out["printedTotal"] = total
    return r.canonicalize(out, d)


def line(identity: dict, quantity: int = 1, condition: int = 0) -> dict:
    return r.make_line(identity, d, quantity, condition)


SOR_FOIL = ident("SOR", 19, "foil", 252)       # shared-numbering era foil: same number as standard
SHD_FOIL = ident("SHD", 27, "foil", 262)
JTL_FOIL = ident("JTL", 525, "foil")           # distinct-numbering era foil: its own number
LOF_FOIL = ident("LOF", 529, "foil")
JTL_100 = ident("JTL", 100, "standard")
SOR_5 = ident("SOR", 5, "standard", 252)
HMW_FOIL = ident("HMW", 610, "foil")
NOT_IN_SAMPLE = ident("JTL", 200, "standard")  # a real, canonical card the receivers do not hold
NOT_IN_SAMPLE_2 = ident("JTL", 201, "standard")


def sides(msg: dict) -> dict:
    return {k: msg[k] for k in ("give", "get")}


def suggest(nid, frm, to, rev, give, get) -> dict:
    return {"format": 1, "type": "SUGGEST", "negotiationId": nid, "from": frm, "to": to,
            "revision": rev, "give": r.normalize_lines(give), "get": r.normalize_lines(get)}


def expect_rejection(data: bytes, table: dict) -> dict:
    """What a receiver answers to `data`, from the codec and hash resolution alone."""
    try:
        msg = r.decode_message(data, d)
        if msg["type"] == "SUGGEST":
            r.resolve_lines(msg, table, d)
    except r.AttError as e:
        return {"att": e.code}
    except r.Rejected as e:
        nid, rev = r._echo(data)
        return {"status": e.status, "detail": e.detail,
                "receipt": r.encode_receipt(e.status, nid, rev, e.detail).hex()}
    raise AssertionError(f"expected {data.hex()} to be rejected")


class Scenario:
    """Records one scenario: every step's event, and the output and state it must produce."""

    def __init__(self, name: str, note: str, table: list, excludes: dict | None = None):
        self.name, self.note, self.steps = name, note, []
        self.devices, self.eps = {}, {}
        sids = {"A": SID_A, "B": SID_B, "C": SID_C}
        matches = {"A": [SID_B], "B": [SID_A, SID_C], "C": [SID_B]}
        full = r.build_reverse_table(table, d)
        for name_ in ("A", "B", "C"):
            excl = (excludes or {}).get(name_, [])
            t = {h: i for h, i in full.items() if h not in excl}
            self.devices[name_] = {"sessionId": sids[name_], "matches": matches[name_],
                                   "catalogExcludes": excl}
            self.eps[name_] = r.NegotiationEndpoint(sids[name_], matches[name_], d, t)
        self.now = 1000

    def ev(self, device: str, event: dict, note: str = "", advance: int = 2) -> dict:
        self.now += advance
        event = {**event, "now": self.now}
        out = self.eps[device].apply(event)
        step = {"device": device, "event": event,
                "expect": {"output": out, "state": r.snapshot(self.eps[device].state)}}
        if note:
            step = {"note": note, **step}
        self.steps.append(step)
        return out

    def deliver(self, src: str, dst: str, out: dict, note: str = "", info: str | None = None,
                advance: int = 2) -> dict:
        """Write `out["send"]` to dst's Negotiation on a connection where src wrote its Info."""
        info_id = self.devices[src]["sessionId"] if info is None else info
        return self.ev(dst, {"type": "incoming", "data": out["send"],
                             "connectionInfoId": info_id}, note, advance)

    def receipt(self, src: str, res: dict, note: str = "", advance: int = 2) -> dict:
        return self.ev(src, {"type": "delivery", "receipt": res["receipt"]}, note, advance)

    def exchange(self, src: str, dst: str, out: dict, note: str = "", advance: int = 2) -> dict:
        res = self.deliver(src, dst, out, note, advance=advance)
        self.receipt(src, res)
        return res

    def doc(self) -> dict:
        return {"name": self.name, "note": self.note, "devices": self.devices,
                "steps": self.steps}


def scenarios(table: list) -> list[dict]:
    out = []
    g1 = [line(SOR_FOIL, 1, 2)]
    t1 = [line(JTL_FOIL, 1, 0), line(JTL_100, 2, 5)]

    s = Scenario("suggestAccept", "A suggests, B accepts; both end ACCEPTED.", table)
    m = s.ev("A", {"type": "suggest", "peer": SID_B, "negotiationId": NID_1, "give": g1,
                   "get": t1}, "L1 from IDLE: a new negotiation, revision 1")
    s.exchange("A", "B", m, "B shows the suggestion mirrored")
    m = s.ev("B", {"type": "accept"}, "L2: ACCEPT carries the digest of A's SUGGEST body")
    s.exchange("B", "A", m, "the author is the sole arbiter: A is ACCEPTED on receipt")
    s.ev("A", {"type": "suggest", "peer": SID_B, "negotiationId": NID_2, "give": t1,
               "get": g1}, "ACCEPTED is terminal: a new suggestion opens a new negotiation")
    out.append(s.doc())

    s = Scenario("counterDecline", "Counter, decline, and a new suggestion after a decline.",
                 table)
    m = s.ev("A", {"type": "suggest", "peer": SID_B, "negotiationId": NID_1, "give": g1,
                   "get": t1})
    s.exchange("A", "B", m)
    m = s.ev("B", {"type": "suggest", "peer": SID_A, "give": [line(JTL_FOIL)],
                   "get": [line(SOR_FOIL, 1, 2)]}, "B counters: revision 2, author B")
    s.exchange("B", "A", m)
    m = s.ev("A", {"type": "decline"}, "A declines revision 2")
    s.exchange("A", "B", m, "B is DECLINED_BY_PEER")
    s.ev("B", {"type": "accept"}, "only the non-author may accept, and only RECEIVED")
    m = s.ev("B", {"type": "suggest", "peer": SID_A, "give": [line(JTL_FOIL)],
                   "get": [line(SOR_FOIL, 1, 2), line(SHD_FOIL)]}, "B sends revision 3")
    s.exchange("B", "A", m)
    out.append(s.doc())

    s = Scenario("crossingSuggests",
                 "Both change the suggestion at once: two SUGGESTs at revision 2. Rule 2: A's "
                 "session id is smaller, so A's wins on both phones.", table)
    m = s.ev("A", {"type": "suggest", "peer": SID_B, "negotiationId": NID_1, "give": g1,
                   "get": t1})
    s.exchange("A", "B", m)
    ma = s.ev("A", {"type": "suggest", "peer": SID_B, "give": g1, "get": [line(JTL_FOIL)]},
              "A changes its own pending suggestion: revision 2")
    mb = s.ev("B", {"type": "suggest", "peer": SID_A, "give": [line(JTL_FOIL)], "get": g1},
              "at the same time B counters: also revision 2", advance=0)
    ra = s.deliver("B", "A", mb, "A holds its own revision 2 and wins the tie: SUPERSEDED")
    rb = s.deliver("A", "B", ma, "B holds its own revision 2 and loses the tie: A's replaces it",
                   advance=0)
    s.receipt("A", rb)
    s.receipt("B", ra, "B is told its suggestion was replaced; it already shows A's", advance=0)
    out.append(s.doc())

    s = Scenario("bothOpenAtOnce",
                 "Both open a negotiation with each other at once, under different ids. Rule 2 "
                 "on (revision, author): A's wins and its id is kept.", table)
    ma = s.ev("A", {"type": "suggest", "peer": SID_B, "negotiationId": NID_1, "give": g1,
                    "get": t1})
    mb = s.ev("B", {"type": "suggest", "peer": SID_A, "negotiationId": NID_2,
                    "give": [line(LOF_FOIL)], "get": [line(SHD_FOIL)]}, advance=0)
    ra = s.deliver("B", "A", mb, "A's own revision 1 wins: SUPERSEDED")
    rb = s.deliver("A", "B", ma, "B adopts A's negotiation id", advance=0)
    s.receipt("A", rb)
    s.receipt("B", ra, "the receipt for B's abandoned negotiation is ignored", advance=0)
    m = s.ev("B", {"type": "accept"})
    s.exchange("B", "A", m)
    out.append(s.doc())

    s = Scenario("acceptCrossesCounter",
                 "B accepts revision 1 while A replaces it with revision 2. A committed revision "
                 "2 first (Rule 1), so B's ACCEPT is STALE and B is shown revision 2.", table)
    m = s.ev("A", {"type": "suggest", "peer": SID_B, "negotiationId": NID_1, "give": g1,
                   "get": t1})
    s.exchange("A", "B", m)
    ma = s.ev("A", {"type": "suggest", "peer": SID_B, "give": g1, "get": [line(JTL_100, 2, 5)]})
    mb = s.ev("B", {"type": "accept"}, advance=0)
    ra = s.deliver("B", "A", mb, "ACCEPT of revision 1, but A's current is revision 2: STALE")
    s.receipt("B", ra, "STALE while ACCEPTING: back to RECEIVED, revision 1 still current")
    s.exchange("A", "B", ma, "revision 2 arrives and supersedes")
    m = s.ev("B", {"type": "accept"})
    s.exchange("B", "A", m)
    out.append(s.doc())

    s = Scenario("lostReceipts",
                 "A receipt is lost; the sender retries the identical bytes and gets DUPLICATE, "
                 "which it treats as OK. Shown for a SUGGEST and for an ACCEPT.", table)
    m = s.ev("A", {"type": "suggest", "peer": SID_B, "negotiationId": NID_1, "give": g1,
                   "get": t1})
    s.deliver("A", "B", m, "B applies it, but A never reads the receipt")
    s.ev("A", {"type": "delivery", "failure": "UNREACHABLE"}, "A only knows delivery failed")
    m2 = s.ev("A", {"type": "retry"}, "the retry sends the same bytes")
    assert m2["send"] == m["send"]
    s.exchange("A", "B", m2, "DUPLICATE: nothing changes on B, A counts it as delivered")
    m = s.ev("B", {"type": "accept"})
    s.deliver("B", "A", m, "A is ACCEPTED; the receipt is lost")
    s.ev("B", {"type": "delivery", "failure": "UNREACHABLE"}, "B stays ACCEPTING")
    m2 = s.ev("B", {"type": "retry"})
    s.exchange("B", "A", m2, "DUPLICATE: B becomes ACCEPTED too")
    out.append(s.doc())

    s = Scenario("cancel", "Ending a negotiation, and a CANCEL that arrives after an accept.",
                 table)
    m = s.ev("A", {"type": "suggest", "peer": SID_B, "negotiationId": NID_1, "give": g1,
                   "get": t1})
    s.exchange("A", "B", m)
    m = s.ev("A", {"type": "end"}, "L4: A ends the trade")
    s.exchange("A", "B", m, "B is CANCELLED")
    s.ev("B", {"type": "accept"}, "nothing left to accept")
    m = s.ev("A", {"type": "suggest", "peer": SID_B, "negotiationId": NID_2, "give": g1,
                   "get": t1}, "a new negotiation")
    s.exchange("A", "B", m)
    acc = s.ev("B", {"type": "accept"})
    s.deliver("B", "A", acc, "A is ACCEPTED; B's receipt read is lost")
    s.ev("B", {"type": "delivery", "failure": "UNREACHABLE"})
    m = s.ev("B", {"type": "end"}, "B ends while ACCEPTING")
    s.exchange("B", "A", m, "CANCEL after ACCEPTED: CLOSED, A stays ACCEPTED. Each user still "
                            "confirms (or not) their own collection change")
    out.append(s.doc())

    s = Scenario("expiry", "10 minutes without a message expires a negotiation (reference "
                           "value); a pruned match expires it too.", table)
    m = s.ev("A", {"type": "suggest", "peer": SID_B, "negotiationId": NID_1, "give": g1,
                   "get": t1})
    s.exchange("A", "B", m)
    s.ev("A", {"type": "tick"}, "not yet", advance=r.IDLE_TIMEOUT_S - 10)
    m = s.ev("A", {"type": "tick"}, "600 s since A's last message: EXPIRED, CANCEL best effort",
             advance=10)
    m_b = s.ev("B", {"type": "tick"}, "B's own timer fires too", advance=0)
    s.exchange("A", "B", m, "B already expired it: CLOSED")
    s.exchange("B", "A", m_b, "and the other way round")
    m = s.ev("A", {"type": "suggest", "peer": SID_B, "negotiationId": NID_2, "give": g1,
                   "get": t1})
    s.exchange("A", "B", m)
    m = s.ev("B", {"type": "matchPruned", "peer": SID_A}, "B pruned its match with A")
    s.exchange("B", "A", m)
    out.append(s.doc())

    s = Scenario("busy", "B is in a negotiation with A; C's suggestion is BUSY.", table)
    m = s.ev("A", {"type": "suggest", "peer": SID_B, "negotiationId": NID_1, "give": g1,
                   "get": t1})
    s.exchange("A", "B", m)
    m = s.ev("C", {"type": "suggest", "peer": SID_B, "negotiationId": NID_3, "give": t1,
                   "get": g1})
    s.exchange("C", "B", m, "BUSY; B's negotiation with A is untouched")
    out.append(s.doc())

    s = Scenario("unknownCards", "B's catalog lacks a card in A's suggestion: UNKNOWN_CARDS, "
                                 "nothing is shown to B.", table,
                 {"B": [r.card_hash(JTL_FOIL, d)]})
    m = s.ev("A", {"type": "suggest", "peer": SID_B, "negotiationId": NID_1, "give": g1,
                   "get": t1})
    s.exchange("A", "B", m, "detail = 1 unresolvable hash")
    m = s.ev("A", {"type": "end"})
    s.exchange("A", "B", m, "B never had this negotiation: CLOSED")
    out.append(s.doc())

    s = Scenario("unsupported", "A peer without the feature, and a message in a format the "
                                "receiver does not implement.", table)
    s.ev("A", {"type": "suggest", "peer": SID_B, "negotiationId": NID_1, "give": g1, "get": t1})
    s.ev("A", {"type": "delivery", "failure": "UNSUPPORTED"},
         "the peer has no Negotiation characteristic: a local result, nothing was written")
    m = {"send": r.pack_message({**suggest(NID_2, SID_A, SID_B, 1, g1, t1), "format": 2}).hex()}
    s.deliver("A", "B", m, "format 2: UNSUPPORTED_FORMAT, detail = highest format B implements "
                           "(1); id and revision echo as zero")
    out.append(s.doc())

    s = Scenario("senderChecks", "Messages that are not addressed to the receiver, or not from "
                                 "a current match on this connection.", table)
    msg = suggest(NID_1, SID_A, SID_C, 1, g1, t1)
    s.deliver("A", "B", {"send": r.encode_message(msg, d).hex()}, "to = C: NOT_FOR_ME")
    msg = suggest(NID_1, SID_A, SID_B, 1, g1, t1)
    s.deliver("A", "B", {"send": r.encode_message(msg, d).hex()},
              "from = A, but the Info written on this connection was C's: UNKNOWN_PEER",
              info=SID_C)
    msg = suggest(NID_1, SID_C, SID_A, 1, g1, t1)
    s.deliver("C", "A", {"send": r.encode_message(msg, d).hex()},
              "C is not one of A's matches: UNKNOWN_PEER")
    out.append(s.doc())

    s = Scenario("rateLimited", "A second message from the same peer within 1 s is RATE_LIMITED "
                                "(reference value); the retry a moment later is applied.", table)
    m = s.ev("A", {"type": "suggest", "peer": SID_B, "negotiationId": NID_1, "give": g1,
                   "get": t1})
    res = s.deliver("A", "B", m)
    m = s.ev("A", {"type": "suggest", "peer": SID_B, "give": g1, "get": [line(JTL_FOIL)]},
             "one outbox per side: nothing new until the previous receipt", advance=0)
    s.receipt("A", res, advance=0)
    m = s.ev("A", {"type": "suggest", "peer": SID_B, "give": g1, "get": [line(JTL_FOIL)]},
             "A changes its suggestion straight away", advance=0)
    s.exchange("A", "B", m, "0 s after B applied the previous message from A", advance=0)
    m = s.ev("A", {"type": "retry"}, "the outbox is still undelivered: same bytes again")
    s.exchange("A", "B", m)
    out.append(s.doc())
    return out


def replay_negotiation(doc: dict) -> None:
    """Replay the negotiation vectors from their own JSON, as a third party would: the codecs
    from bytes, and every scenario from fresh devices built only from the file's data."""
    doc = json.loads(json.dumps(doc))                      # exactly what gets written
    table = r.build_reverse_table([c["identity"] for c in doc["catalog"]["identities"]], d)
    assert all(r.card_hash(c["identity"], d) == c["hash"] for c in doc["catalog"]["identities"])
    for v in doc["messages"]:
        assert r.decode_message(bytes.fromhex(v["bytesHex"]), d) == v["message"]
        assert r.encode_message(v["message"], d).hex() == v["bytesHex"]
    for v in doc["receipts"]:
        assert r.decode_receipt(bytes.fromhex(v["bytesHex"])) == v["receipt"]
    for v in doc["rejections"]:
        assert expect_rejection(bytes.fromhex(v["bytesHex"]), table) == v["expect"], v["note"]
    for v in doc["features"]:
        assert r.negotiated_format(v["ownFormat"], v["peerInfo"]) == v["expectFormat"]
    steps = 0
    for sc in doc["scenarios"]["list"]:
        eps = {}
        for name, dev in sc["devices"].items():
            t = {h: i for h, i in table.items() if h not in dev["catalogExcludes"]}
            eps[name] = r.NegotiationEndpoint(dev["sessionId"], dev["matches"], d, t)
        for st in sc["steps"]:
            out = eps[st["device"]].apply(st["event"])
            assert out == st["expect"]["output"], (sc["name"], st)
            assert r.snapshot(eps[st["device"]].state) == st["expect"]["state"], (sc["name"], st)
            steps += 1
    print(f"selftest: negotiation vectors replay from their own data ({steps} scenario steps)")


def negotiation_vectors(cards: list[dict]) -> tuple[dict, str]:
    table = r.build_reverse_table(cards, d)
    by_hash = {h: i for h, i in table.items()}

    def message_vector(msg: dict, note: str) -> dict:
        data = r.encode_message(msg, d)
        assert r.decode_message(data, d) == msg, "decode(encode(m)) != m"
        v = {"note": note, "message": msg, "bytesHex": data.hex(), "length": len(data)}
        if msg["type"] == "SUGGEST":
            v["digest"] = r.suggest_digest(data[r.NEG_HEADER_BYTES:])
            v["resolved"] = r.resolve_lines(msg, table, d)
            v["receiverView"] = r.mirror(v["resolved"])
        return v

    small = suggest(NID_1, SID_A, SID_B, 1,
                    [line(SOR_FOIL, 1, 2), line(SOR_5, 3, 0)],
                    [line(JTL_FOIL, 1, 0), line(JTL_100, 2, 5), line(JTL_100, 1, 2)])
    small_bytes = r.encode_message(small, d)
    give40 = [line(ident("LOF", n, "standard"), 1 + i % 4, i % 8)
              for i, n in enumerate(list(range(2, 10)) + list(range(11, 23)))]
    get40 = [line(ident("JTL", n, "standard"), 1 + i % 3, (7 - i) % 8)
             for i, n in enumerate(list(range(2, 10)) + list(range(11, 23)))]
    big = suggest(NID_1, SID_A, SID_B, 7, give40, get40)

    messages = [
        message_vector(small, "SUGGEST, revision 1: a shared-numbering-era foil (SOR 19, same "
                              "number as its standard) and a distinct-numbering-era foil (JTL "
                              "525, its own number); JTL 100 twice, in two conditions, as two "
                              "lines ordered by (hash, condition)"),
        message_vector(big, "SUGGEST at the 40-line maximum: 54 + 11 x 40 = 494 bytes, one "
                            "attribute value"),
        message_vector(suggest(NID_1, SID_A, SID_B, 2, [line(HMW_FOIL, 255, 1)], []),
                       "quantity 255 and no get lines: the wire carries 1..255 whatever an "
                       "app's screens allow, and a gift is a valid suggestion"),
        message_vector(suggest(NID_1, SID_B, SID_A, 3, [], [line(LOF_FOIL, 1, 9)]),
                       "condition 9 is reserved: valid on the wire, read as 0 (not stated)"),
        message_vector({"format": 1, "type": "ACCEPT", "negotiationId": NID_1, "from": SID_B,
                        "to": SID_A, "revision": 1,
                        "digest": r.suggest_digest(small_bytes[r.NEG_HEADER_BYTES:])},
                       "ACCEPT of the first SUGGEST: its digest is FNV-1a 64 of that "
                       "message's bytes from offset 52"),
        message_vector({"format": 1, "type": "DECLINE", "negotiationId": NID_1, "from": SID_B,
                        "to": SID_A, "revision": 1, "reason": 0}, "DECLINE, reason 0"),
        message_vector({"format": 1, "type": "DECLINE", "negotiationId": NID_1, "from": SID_B,
                        "to": SID_A, "revision": 1, "reason": 7},
                       "DECLINE, reserved reason 7: valid, read as 0"),
        message_vector({"format": 1, "type": "CANCEL", "negotiationId": NID_1, "from": SID_A,
                        "to": SID_B, "revision": 4}, "CANCEL: header only, 52 bytes"),
    ]

    receipts = []
    for code, status in sorted(r.STATUSES.items()):
        detail = {"UNKNOWN_CARDS": 2, "UNSUPPORTED_FORMAT": 1}.get(status, 0)
        nid, rev = (r.ZERO_ID, 0) if status == "UNSUPPORTED_FORMAT" else (NID_1, 3)
        raw = r.encode_receipt(status, nid, rev, detail)
        rc = r.decode_receipt(raw)
        receipts.append({"receipt": rc, "bytesHex": raw.hex(),
                         "delivered": r.receipt_delivered(rc)})
    unknown = bytes([1, 0x0C]) + r._id_bytes(NID_1) + (3).to_bytes(2, "big") + b"\x00"
    receipts.append({"note": "reserved status 0x0C: a failed delivery, never OK",
                     "receipt": r.decode_receipt(unknown), "bytesHex": unknown.hex(),
                     "delivered": False})
    bad_receipts = []
    ok = r.encode_receipt("OK", NID_1, 3)
    for raw, note in [(b"", "empty: read before any write on this connection"),
                      (ok[:-1], "20 bytes"), (ok + b"\x00", "22 bytes"),
                      (b"\x02" + ok[1:], "format 2")]:
        try:
            r.decode_receipt(raw)
        except ValueError:
            bad_receipts.append({"note": note + ": not a receipt, a failed delivery",
                                 "bytesHex": raw.hex()})
        else:
            raise AssertionError(note)

    # --- rejections ---
    rejections = []

    def rej(data: bytes, note: str, expect_status: str | None = None) -> None:
        got = expect_rejection(data, table)
        if expect_status is not None:
            assert got.get("status", got.get("att")) == expect_status, (note, got)
        rejections.append({"note": note, "bytesHex": data.hex(), "expect": got})

    def sug(give, get, **kw) -> bytes:
        return r.pack_message({**suggest(NID_1, SID_A, SID_B, 1, [], []), "give": give,
                               "get": get, **kw})

    rej(b"", "empty value", "invalidAttributeValueLength")
    rej(small_bytes[:51], "51 bytes: shorter than the header", "invalidAttributeValueLength")
    rej(sug(give40 * 3, []), "more than 512 bytes", "invalidAttributeValueLength")
    rej(b"\x02" + small_bytes[1:], "format 2", "UNSUPPORTED_FORMAT")
    rej(b"\x02", "format 2, a single byte: the format is judged before the length",
        "UNSUPPORTED_FORMAT")
    rej(b"\x00" + small_bytes[1:], "format 0", "MALFORMED")
    rej(small_bytes[:1] + b"\x05" + small_bytes[2:], "unknown type 5", "MALFORMED")
    rej(sug([line(SOR_FOIL)], [], revision=0), "revision 0", "MALFORMED")
    rej(small_bytes[:-1], "SUGGEST one byte short of 54 + 11 x lines", "MALFORMED")
    rej(small_bytes + b"\x00", "SUGGEST with one trailing byte (no extension bytes exist)",
        "MALFORMED")
    rej(r.pack_message({"format": 1, "type": "ACCEPT", "negotiationId": NID_1, "from": SID_B,
                        "to": SID_A, "revision": 1, "digest": "00" * 7}),
        "ACCEPT of 59 bytes", "MALFORMED")
    rej(r.pack_message({"format": 1, "type": "CANCEL", "negotiationId": NID_1, "from": SID_B,
                        "to": SID_A, "revision": 1}) + b"\x00", "CANCEL of 53 bytes",
        "MALFORMED")
    rej(sug([], []), "no lines", "MALFORMED")
    l41 = r.normalize_lines(give40 + get40 + [line(SOR_5)])
    rej(sug(l41, []), "41 lines (505 bytes, so within 512)", "MALFORMED")
    rej(sug([{**line(SOR_FOIL), "quantity": 0}], []), "quantity 0", "MALFORMED")
    two = r.normalize_lines([line(SOR_FOIL), line(JTL_FOIL)])
    rej(sug(list(reversed(two)), []), "give lines not ascending by hash", "MALFORMED")
    rej(sug([], [line(JTL_FOIL), line(JTL_FOIL)]), "the same (hash, condition) twice",
        "MALFORMED")
    rej(sug([line(JTL_100, 1, 5), line(JTL_100, 1, 2)], []),
        "one hash in two conditions, in descending condition order", "MALFORMED")
    rej(sug([{**line(JTL_100), "finish": 2}], []), "finish byte 2: swu has two finishes",
        "MALFORMED")
    rej(sug([{**line(JTL_100), "finish": 1}], []),
        "finish byte 1 (foil) on the hash of a standard card: the hash decides the card",
        "MALFORMED")
    rej(sug([line(NOT_IN_SAMPLE)], [line(JTL_100)]),
        "a canonical card the receiver's catalog does not hold", "UNKNOWN_CARDS")
    rej(sug(r.normalize_lines([line(NOT_IN_SAMPLE), line(NOT_IN_SAMPLE_2)]),
            r.normalize_lines([line(NOT_IN_SAMPLE, 1, 0), {**line(JTL_100), "finish": 1}])),
        "detail counts distinct unknown hashes (2); unknown cards are judged before a finish "
        "mismatch", "UNKNOWN_CARDS")

    normalization = []
    lines_in = [line(JTL_100, 1, 5), line(SOR_FOIL, 1, 0), line(JTL_100, 2, 5),
                line(JTL_100, 1, 0)]
    normalization.append({"note": "same (hash, condition) merges by adding quantities; result "
                                  "ascending by (hash, condition)",
                          "input": lines_in, "expect": r.normalize_lines(lines_in)})
    for bad, note in [([line(JTL_100, 200), line(JTL_100, 56)], "merged quantity 256"),
                      ([line(JTL_100), {**line(JTL_100, 1, 3), "finish": 1}],
                       "one hash with two finish bytes")]:
        try:
            r.normalize_lines(bad)
        except ValueError:
            normalization.append({"note": note + ": refused", "input": bad, "expect": None})
        else:
            raise AssertionError(note)

    features = []
    for info, note in [
            ({"v": 1, "game": "swu", "features": {"negotiation": 1}}, "declares format 1"),
            ({"v": 1, "game": "swu", "features": {"negotiation": 3, "someLaterFeature": 2}},
             "a peer with a newer format: use min(own, peer); unknown names are ignored"),
            ({"v": 1, "game": "swu"}, "no features: not supported"),
            ({"v": 1, "game": "swu", "features": ["negotiation"]}, "not an object"),
            ({"v": 1, "game": "swu", "features": {"negotiation": 0}}, "0"),
            ({"v": 1, "game": "swu", "features": {"negotiation": -1}}, "negative"),
            ({"v": 1, "game": "swu", "features": {"negotiation": "1"}}, "a string"),
            ({"v": 1, "game": "swu", "features": {"negotiation": True}}, "a boolean"),
            ({"v": 1, "game": "swu", "features": {"negotiation": 1.5}}, "not integral"),
            ({"v": 1, "game": "swu", "features": {"negotiation": None}}, "null"),
            ({"v": 1, "game": "swu", "features": {"trade": 1}}, "another feature only")]:
        features.append({"note": note, "peerInfo": info, "ownFormat": r.NEGOTIATION_FORMAT,
                         "expectFormat": r.negotiated_format(r.NEGOTIATION_FORMAT, info)})

    scen = scenarios(cards)
    n_steps = sum(len(x["steps"]) for x in scen)
    ns = uuid.uuid5(PROTOCOL_NAMESPACE, "characteristic:Negotiation")
    doc = {
        "$comment": "Generated by make_vectors.py from reference.py. Do not hand-edit. "
                    "SPEC.md 6.5: trade negotiation, optional feature 'negotiation', format 1.",
        "protocol": {
            "feature": r.FEATURE_NEGOTIATION, "format": r.NEGOTIATION_FORMAT,
            "characteristic": {"Negotiation": str(ns)},
            "headerBytes": r.NEG_HEADER_BYTES, "lineBytes": r.NEG_LINE_BYTES,
            "maxLines": r.NEG_MAX_LINES, "maxValueBytes": r.NEG_MAX_VALUE_BYTES,
            "receiptBytes": r.RECEIPT_BYTES,
            "messageTypes": {v: k for k, v in r.MSG_TYPES.items()},
            "statuses": {v: k for k, v in r.STATUSES.items()},
            "conditions": {str(k): v for k, v in r.CONDITIONS.items()},
            "referenceValues": {"idleTimeoutSeconds": r.IDLE_TIMEOUT_S,
                                "rateWindowSeconds": r.RATE_WINDOW_S,
                                "rateMaxPerWindow": r.RATE_MAX_PER_WINDOW,
                                "rateMinIntervalSeconds": r.RATE_MIN_INTERVAL_S},
            "$comment": "The characteristic UUID is UUIDv5(PROTOCOL_NAMESPACE_UUID, "
                        "'characteristic:Negotiation'), recomputed here like the others in "
                        "swu-v1.json. Card hashes are SPEC.md 6.2's, as in swu-v1.json.",
        },
        "definition": {"game": d.game, "definitionVersion": d.doc["definitionVersion"]},
        "catalog": {
            "note": "Every identity a receiver in these vectors can resolve (SPEC.md 6.5.5), "
                    "with its wire hash. Rejection vectors resolve against all of it; a "
                    "scenario device resolves against all of it minus its catalogExcludes.",
            "identities": [{"identity": i, "hash": h} for h, i in sorted(by_hash.items())],
        },
        "features": features,
        "messages": messages,
        "receipts": receipts,
        "invalidReceipts": bad_receipts,
        "lineNormalization": normalization,
        "rejections": rejections,
        "scenarios": {
            "note": "Plain data. For each scenario, create one device per entry in 'devices' "
                    "(state IDLE, the given session id and matches, catalog minus "
                    "catalogExcludes), then feed every step's event to its device in order and "
                    "compare the output and the state snapshot. Event types: suggest, accept, "
                    "decline, end, stop, retry, tick, matchPruned (local); incoming (a value "
                    "written to Negotiation, on a connection whose central wrote Info with id "
                    "connectionInfoId); delivery (the receipt read back, or a local failure). "
                    "'now' is in seconds. Output: send (bytes to deliver), receipt (bytes the "
                    "receiver stores for the read), att (an ATT error, no receipt), refused (a "
                    "local action not allowed in this state; nothing changes).",
            "list": scen,
        },
    }
    replay_negotiation(doc)
    summary = (f"{len(messages)} messages, {len(receipts)} receipts, "
               f"{len(bad_receipts)} invalid receipts, {len(rejections)} rejections, "
               f"{len(features)} feature cases, {len(scen)} scenarios / {n_steps} steps")
    return doc, summary


def main() -> int:
    selftest_variant_key()
    cards, skipped = catalog()
    by_key = {json.dumps(c, sort_keys=True): c for c in cards}
    print(f"catalog sample: {len(by_key)} distinct identities  (recorded gaps {skipped})")

    encodings = [
        encoding_vector({"setCode": "SOR", "collectorNumber": 5, "printedTotal": 252,
                         "finish": "standard"},
                        "shared-numbering mainline; setCode is shared by four print runs so "
                        "printedTotal is required"),
        encoding_vector({"setCode": "SOR", "collectorNumber": 7, "printedTotal": 20,
                         "finish": "foil"},
                        "same printed code, different print run — only printedTotal separates "
                        "this from the mainline card above"),
        encoding_vector({"setCode": "JTL", "collectorNumber": 100, "finish": "standard"},
                        "distinct-numbering era; code belongs to one run so printedTotal is absent "
                        "(note the 0x00 presence byte)"),
        encoding_vector({"setCode": "JTL", "collectorNumber": 336, "finish": "foil"},
                        "the foil twin is its OWN collector number — never derived by flipping "
                        "finish on 100 (SPEC.md 4.4.1)"),
        encoding_vector({"setCode": "jtlp", "collectorNumber": 4, "finish": "FOIL"},
                        "case normalization: setCode upper, finish lower, per identifier.fields"),
        encoding_vector({"setCode": "SHD", "collectorNumber": 262, "printedTotal": 262,
                         "finish": "standard"},
                        "collectorNumber equal to printedTotal — the last card in its set"),
        encoding_vector({"setCode": "HMW", "collectorNumber": 610, "finish": "foil"},
                        "definitionVersion 6: a Hyperspace Foil in the distinct-numbering era has "
                        "its own number (HMW 610 Torrent, the foil twin of Hyperspace 372)"),
        encoding_vector({"setCode": "HMWP", "collectorNumber": 25, "finish": "foil"},
                        "definitionVersion 6: weekly-play foil, the foil twin of HMWP 5"),
        encoding_vector({"setCode": "CST", "collectorNumber": 2, "finish": "foil"},
                        "definitionVersion 6: a single-run, single-finish code marked 'shared' "
                        "that still carries no printedTotal because the code has one run"),
    ]

    rejections = [
        rejection_vector({"setCode": "JTL", "collectorNumber": 100, "printedTotal": 262,
                          "finish": "standard"},
                         "printedTotal present on a code belonging to one print run"),
        rejection_vector({"setCode": "SOR", "collectorNumber": 5, "finish": "standard"},
                         "printedTotal absent on a shared code"),
        rejection_vector({"setCode": "SOR", "collectorNumber": 5, "printedTotal": 999,
                          "finish": "standard"},
                         "printedTotal matches no print run under this code"),
        rejection_vector({"setCode": "ZZZ", "collectorNumber": 1, "finish": "standard"},
                         "setCode matches no entry in sets"),
        rejection_vector({"setCode": "JTL", "collectorNumber": 100, "finish": "holo"},
                         "finish outside the closed vocabulary"),
        rejection_vector({"setCode": "JTL", "collectorNumber": 100, "finish": "standard",
                          "printedTotal": None},
                         "optional field present as null instead of absent"),
        rejection_vector({"setCode": "JTL", "collectorNumber": 100, "finish": "standard",
                          "cardName": "Vader"},
                         "undeclared field; name is display metadata, never identity"),
        rejection_vector({"setCode": "JTL", "finish": "standard"},
                         "missing required field"),
        rejection_vector({"setCode": "JTL", "collectorNumber": 100, "finish": "standard",
                          "variantKey": "some-variant"},
                         "variantKey present when no registered collision matches — it is for "
                         "registered exceptions only, and a spurious one moves every filter bit"),
    ]

    # A filter built from a real want list, plus a genuine false positive found by search.
    want = [by_key[k] for k in sorted(by_key)[:r.MAX_WANT_ENTRIES]]
    filt = r.BloomFilter.build(want, d)
    wanted = {json.dumps(w, sort_keys=True) for w in want}

    false_positive = next(
        (c for k, c in sorted(by_key.items())
         if k not in wanted and filt.might_contain(c, d)), None)
    true_negative = next(
        (c for k, c in sorted(by_key.items())
         if k not in wanted and not filt.might_contain(c, d)), None)
    assert false_positive is not None, "no false positive found; widen the search"

    doc = {
        "$comment": "Generated by make_vectors.py from reference.py. Do not hand-edit.",
        "protocol": {"filterBits": r.FILTER_BITS, "hashCount": r.HASH_COUNT,
                     "maxWantEntries": r.MAX_WANT_ENTRIES, "maxBitsSet": r.MAX_BITS_SET,
                     "serviceUUID": str(uuid.uuid5(PROTOCOL_NAMESPACE, d.game)),
                     "characteristics": {
                         name: str(uuid.uuid5(PROTOCOL_NAMESPACE, "characteristic:" + name))
                         for name in ("Info", "WantFilter", "MyCandidates", "PeerCandidates",
                                      "Confirmed", "PeerConfirmed")},
                     "$comment": "UUIDs are DERIVED, not assigned — recomputing them here rather "
                                 "than hard-coding means the vectors prove the derivation instead "
                                 "of just asserting its output. Card hashes on the wire are the "
                                 "fnv1a64Hex values below, big-endian, 8 bytes each (SPEC.md 6.2)."},
        "definition": {"game": d.game, "definitionVersion": d.doc["definitionVersion"]},
        "encodings": encodings,
        "rejections": rejections,
        "filter": {
            "note": "A filter built from the want list below. bitsSet is well under maxBitsSet, "
                    "as honest construction at the cap always is (SPEC.md 5.4).",
            "want": want,
            "filterHex": filt.hex(),
            "bitsSet": filt.bits_set,
            "queries": [
                {"note": "a member — must be a hit", "identity": want[0], "expectHit": True},
                {"note": "a genuine FALSE POSITIVE: not in the want list, but every one of its "
                         "four bits is set by other members. An implementation that does not "
                         "report a hit here has diverged in the hash or the bit layout.",
                 "identity": false_positive, "expectHit": True},
                {"note": "a true negative", "identity": true_negative, "expectHit": False},
            ],
        },
        "known_gaps": {
            "tokensOutOfScope": {
                "count": skipped["non_numeric"],
                "note": "Tokens (collector numbers T001 etc.) are deliberately not expressible: "
                        "collectorNumber is declared an integer, which excludes them by "
                        "construction. This is a settled scope decision, not a gap to close — see "
                        "SPEC.md 1 and 4.4.3.",
            },
            "unknownFinish": {
                "count": skipped["unknown_finish"],
                "note": "Cards whose finish the source catalog does not record. Excluded here "
                        "because the vectors must not assert a finish nothing verified.",
            },
            "excludedPrintRuns": {
                "runs": ["SOR printedTotal=2", "SHD printedTotal=2"],
                "note": "Prerelease and judge promos, absent from the definition file's sets and "
                        "so not canonicalizable at all. The source data for them is wrong and "
                        "incomplete, and two of them collide on set code and collector number — "
                        "see SPEC.md 4.6.1. A holding position pending real data, not a ruling "
                        "that these cards are untradeable.",
            },
        },
    }

    neg_doc, neg_summary = negotiation_vectors(cards)
    files = [
        (HERE / "vectors" / "swu-v1.json", doc,
         f"{len(encodings)} encodings, {len(rejections)} rejections, "
         f"filter bitsSet={filt.bits_set}/{r.FILTER_BITS}"),
        (HERE / "vectors" / "negotiation-v1.json", neg_doc, neg_summary),
    ]

    if "--check" in sys.argv:
        # CI guard. These bytes are a published contract: once an implementation ships against
        # them, a changed vector means every peer running the old bytes silently stops matching.
        failed = 0
        for out, content, _ in files:
            text = json.dumps(content, indent=2) + "\n"
            if not out.exists():
                print(f"FAIL {out.relative_to(ROOT)} does not exist", file=sys.stderr)
                failed += 1
            elif out.read_text() != text:
                print(f"FAIL {out.relative_to(ROOT)} is out of date with reference.py.\n"
                      f"     If this is a deliberate protocol change, regenerate without --check "
                      f"AND bump the protocol version — do not just refresh the file.",
                      file=sys.stderr)
                failed += 1
            else:
                print(f"ok   {out.relative_to(ROOT)} matches reference.py")
        return 1 if failed else 0

    for out, content, summary in files:
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(content, indent=2) + "\n")
        print(f"wrote {out.relative_to(ROOT)}: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
