#!/usr/bin/env python3
"""Generate conformance test vectors from reference.py.

Run from the repository root:  python3 trade-protocol/make_vectors.py

The output (vectors/swu-v1.json) is committed. Regenerate it only when the protocol's encoding or
parameters change, and treat any unexplained diff in the regenerated file as a protocol break, not
a refresh — the whole point of the vectors is that these bytes do not move.

Real card identities are derived from the SWUScan reference catalog so the false-positive vector
exercises the hash against genuine data rather than synthetic strings.
"""
from __future__ import annotations

import json
import pathlib
import sys
import uuid

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import reference as r  # noqa: E402

ROOT = HERE.parent
DEFN = HERE / "definitions" / "swu.json"
MANIFEST = ROOT / "SWU scan" / "card_manifest.json"
FINISHES = ROOT / "SWU scan" / "finish_map.json"

d = r.Definition.load(DEFN)

#: Fixed forever; see SPEC.md 3.1.
PROTOCOL_NAMESPACE = uuid.UUID("8a44bf6a-7c5c-40fa-84d1-f3a5ec791831")


def catalog() -> list[dict]:
    """Every SWUScan card expressed as a protocol identity.

    The mapping the app will need in Swift, in miniature: a synthetic set code ("SOR-20") splits
    into a printed code plus the printedTotal that distinguishes that print run, and finish comes
    from finish_map rather than being assumed.
    """
    cards = json.loads(MANIFEST.read_text())["cards"]
    fmap = json.loads(FINISHES.read_text())
    out, skipped = [], {"non_numeric": 0, "unknown_finish": 0, "not_canonical": 0}

    for card_id in cards:
        set_part, number = card_id.split("/", 1)
        if not number.isdigit():
            skipped["non_numeric"] += 1        # tokens: T001 etc., see vectors' "known_gaps"
            continue

        code, total = set_part, None
        if "-" in set_part:
            base, suffix = set_part.rsplit("-", 1)
            if suffix.isdigit():
                code, total = base, int(suffix)
        if total is None and d.code_is_shared(code):
            # A shared printed code needs its total, and the app's cardId does not carry one for
            # the mainline run — "SOR/005", not "SOR-252/005". It has to be joined from the
            # catalog. Worth stating plainly because the Swift mapping layer inherits the same
            # constraint: cardId alone cannot produce a protocol identity for a shared code.
            total = cards[card_id].get("card_count") or None

        entry = fmap.get(card_id, {})
        if entry.get("dual"):
            finishes = ["standard", "foil"]    # one number, both finishes (SPEC.md 4.4.1 "shared")
        elif entry.get("foil") is None:
            skipped["unknown_finish"] += 1
            continue
        else:
            finishes = ["foil" if entry["foil"] else "standard"]

        for finish in finishes:
            ident = {"setCode": code, "collectorNumber": int(number), "finish": finish}
            if total is not None:
                ident["printedTotal"] = total
            try:
                out.append(r.canonicalize(ident, d))
            except r.NotCanonical:
                skipped["not_canonical"] += 1
    return out, skipped


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


def main() -> int:
    selftest_variant_key()
    cards, skipped = catalog()
    by_key = {json.dumps(c, sort_keys=True): c for c in cards}
    print(f"catalog: {len(by_key)} distinct identities  (skipped {skipped})")

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

    out = HERE / "vectors" / "swu-v1.json"
    text = json.dumps(doc, indent=2) + "\n"

    if "--check" in sys.argv:
        # CI guard. These bytes are a published contract: once an implementation ships against
        # them, a changed vector means every peer running the old bytes silently stops matching.
        if not out.exists():
            print(f"FAIL {out.relative_to(ROOT)} does not exist", file=sys.stderr)
            return 1
        if out.read_text() != text:
            print(f"FAIL {out.relative_to(ROOT)} is out of date with reference.py.\n"
                  f"     If this is a deliberate protocol change, regenerate without --check AND "
                  f"bump the protocol version — do not just refresh the file.", file=sys.stderr)
            return 1
        print(f"ok   {out.relative_to(ROOT)} matches reference.py")
        return 0

    out.parent.mkdir(exist_ok=True)
    out.write_text(text)
    print(f"wrote {out.relative_to(ROOT)}: {len(encodings)} encodings, {len(rejections)} "
          f"rejections, filter bitsSet={filt.bits_set}/{r.FILTER_BITS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
