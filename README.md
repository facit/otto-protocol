# Open TCG Trade Protocol

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
| [`reference.py`](reference.py) | Executable reference implementation of canonicalization, encoding, hashing and the filter. Dependency-free, game-agnostic, no transport. |
| [`vectors/swu-v1.json`](vectors/swu-v1.json) | Conformance test vectors. Generated from `reference.py`. |
| [`validate.py`](validate.py) | Checks a definition file against the rules §4.6 says the JSON Schema structurally cannot enforce. |
| [`make_vectors.py`](make_vectors.py) | Regenerates the vectors; `--check` asserts they still match `reference.py`. |

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

## Definition files

One per game, validating against the schema. To add a game or correct one:

```bash
python3 trade-protocol/validate.py                 # all definition files
python3 trade-protocol/make_vectors.py --check     # vectors still match the reference
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
