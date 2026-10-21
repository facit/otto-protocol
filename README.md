# OTTO — Open TCG Trading Over-the-air

A vendor-neutral, offline, peer-to-peer protocol for two people standing near each other to
discover that they can trade physical trading-card-game cards, using nothing but Bluetooth LE.
No server, no accounts, no internet connection on either device.

**[`SPEC.md`](SPEC.md) is the normative document.** Everything else here exists to make it
implementable without guessing.

| File | What it is |
|---|---|
| [`SPEC.md`](SPEC.md) | The protocol. Read §4 and §5 before writing any matching code. |
| [`schema/game-definition.schema.json`](schema/game-definition.schema.json) | JSON Schema every per-game definition file must validate against. |
| [`definitions/swu.json`](definitions/swu.json) | The *Star Wars: Unlimited* definition file, and the worked example throughout the spec. |
| [`reference.py`](reference.py) | Executable reference implementation of canonicalization, encoding, hashing and the filter, and of the optional negotiation feature (§6.5): message and receipt codecs, line ordering, hash resolution, feature discovery and the state machine as a pure function. Dependency-free, game-agnostic, no transport. |
| [`vectors/swu-v1.json`](vectors/swu-v1.json) | Conformance test vectors for §4–§6.2. Generated from `reference.py`. |
| [`vectors/negotiation-v1.json`](vectors/negotiation-v1.json) | Conformance vectors for the negotiation feature (§6.5, format 1), with `swu` as the worked example: message, receipt and rejection bytes, feature discovery, and state-machine scenarios as plain data. Generated from `reference.py`. |
| [`validate.py`](validate.py) | Checks a definition file against the rules §4.6 says the JSON Schema structurally cannot enforce; with `--base OLD.json`, also that `finishes` stayed append-only and `definitionVersion` increased. |
| [`make_vectors.py`](make_vectors.py) | Regenerates both vector files; `--check` asserts they still match `reference.py`. It also runs self-tests, including a replay of the negotiation vectors from their own data. |
| [`testdata/swu-catalog-sample.json`](testdata/swu-catalog-sample.json) | A small vendored sample of real card identities that `make_vectors.py` builds the vectors from. Test data, not a catalog. |
| [`check_app_bundle.py`](check_app_bundle.py) | App tooling, not part of the protocol: `python3 check_app_bundle.py /path/to/app-repo` checks that an app's bundled copies of the definition and vectors are byte-identical to the files here. |

Everything here runs from this repository alone with a stock Python 3 (no packages; `validate.py`
additionally uses `jsonschema` when it is installed).

## If you are implementing this

Start with the vectors. Every failure mode in this protocol is **silent**: a card encoded even
slightly differently from its canonical form hashes differently, misses in the Bloom filter, and
the trade is simply never discovered. Nothing reports an error, on either device. Prose cannot
protect you from that; matching bytes can.

Work in this order:

1. **Canonical encoding.** Implement §5.5, then reproduce every `canonicalBytesHex` in
   `vectors/swu-v1.json`. These are short — `01 "SOR" 01 00000007 01 00000014 01 "standard" 00` is
   a whole card — so a mismatch is easy to eyeball. Note the presence byte on *every* field,
   including required ones, and the `0x00` where an absent optional field would be.
2. **FNV-1a 64 and bit derivation.** Reproduce `fnv1a64Hex` and `bitIndices` for the same
   identities. Do not substitute your language's built-in hash; §5.2 explains why that silently
   fails across platforms and even across runs.
3. **The filter.** Build one from the `filter.want` list and check you get `filterHex` byte for
   byte. Then run `filter.queries` — one of them is a **genuine false positive**, a card not in the
   want list whose four bits all happen to be set. If your implementation reports a miss there, it
   has diverged even though the earlier vectors may have passed.
4. **Rejection.** Every entry in `rejections` must be refused, not repaired. §4.5 explains why
   repairing is worse than rejecting: a "fixed" identity is a different card.
5. **Transport.** Only now §3, §6 and §7. Nothing in the reference implementation covers these.
6. **Negotiation (optional, §6.5).** Only if you offer trade suggestions. In
   `vectors/negotiation-v1.json`: first the codecs — decode every `messages` entry from
   `bytesHex` and encode it back, decode every `receipts` entry, refuse every `invalidReceipts`
   value, and give every `rejections` entry exactly its expected ATT error or receipt bytes
   (the order of checks in §6.5.6 matters: some inputs break two rules). Check `features` and
   `lineNormalization`. Then the `scenarios`: build each device from its entry in `devices`, feed
   every step's event to it in order, and compare the output and the state snapshot; they are
   plain data so your own state machine can replay them without any of this repository's code.
   Only then the transport of §6.5.3: the `Negotiation` characteristic, delivery by session id,
   and a real 494-byte write on real hardware.

## Definition files

One per game, validating against the schema. To add a game or correct one:

```bash
python3 validate.py                 # all definition files
python3 make_vectors.py --check     # vectors still match the reference
```

Both belong in CI. `validate.py` does not implement §4.6 rule 6 (`definitionVersion` increased, no
already-published value redefined) because that rule compares a file against its own previous
revision — put it in the job that knows the base revision.

`canonicalSource` in each definition file is the tiebreaker for factual disputes about what cards
and sets exist. Disputes are settled by checking there, not by discussion.

## Status

Draft. Not yet implemented end-to-end, and two things in [§9](SPEC.md#9-open-items) should be
settled before anyone ships against it — notably whether an iOS peripheral can advertise its filter
at all, which is unverified on real hardware. The protocol is designed so the answer does not
affect correctness, only scan cost.

`definitions/swu.json` is honest about its own gaps: 9 of its 31 print runs are marked
`"verified": false`, meaning the exact official name has not been confirmed against
`canonicalSource` (the set codes themselves are sound). Those need checking against `canonicalSource` before the file
is treated as authoritative. Tokens are deliberately not expressible (§4.4.3) — that is a scope
decision, not a gap.
