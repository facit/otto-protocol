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

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import reference as r  # noqa: E402

ROOT = HERE.parent
DEFN = HERE / "definitions" / "swu.json"
MANIFEST = ROOT / "SWU scan" / "card_manifest.json"
FINISHES = ROOT / "SWU scan" / "finish_map.json"

d = r.Definition.load(DEFN)


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


def main() -> int:
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
                     "serviceUUID": "0a3edc52-5120-5164-972e-8dfddc85be41"},
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
            "nonIntegerCollectorNumbers": {
                "count": skipped["non_numeric"],
                "note": "Cards whose printed collector number is not an integer (tokens numbered "
                        "T001 etc.) cannot be expressed while identifier.fields declares "
                        "collectorNumber as type 'integer'. They are excluded from these vectors "
                        "and are an unresolved question, not a decision — see SPEC.md 9.",
            },
            "unknownFinish": {
                "count": skipped["unknown_finish"],
                "note": "Cards whose finish the source catalog does not record. Excluded here "
                        "because the vectors must not assert a finish nothing verified.",
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
