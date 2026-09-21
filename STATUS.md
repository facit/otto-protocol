# Trade protocol — working status

Where this stands, what is actually verified, and what to pick up next. Written to survive the
session it was produced in; the *reasoning* behind each decision is in `SPEC.md` and in the commit
messages, which are deliberately long for that reason.

Last updated at `trade-protocol/implementation` head "Add the BLE transport layer".

## Branches

| Branch | Contains | Based on |
|---|---|---|
| `trade-protocol/docs` | spec, schema, definition file, Python reference, vectors, validators | `next` |
| `trade-protocol/implementation` | all of the above **plus** the Swift in `SWU scan/` | `next` (docs is a linear ancestor) |

`docs` deliberately contains no Swift. Keep it that way — merging `implementation` into `docs`
fast-forwards it and collapses the distinction.

Nothing is merged to `next` or `main`, and there are no PRs. All the work exists only on these two
branches.

## What is verified, and what is not

**Verified here** (Linux container, no Apple toolchain):

- `python3 trade-protocol/validate.py` — definition file against the rules §4.6 says the JSON
  Schema structurally cannot enforce.
- `python3 trade-protocol/make_vectors.py --check` — the committed vectors still match
  `reference.py`. Also runs a self-test of §4.5 rule 7 in both directions.
- `python3 trade-protocol/check_app_bundle.py` — the app's bundled JSON copies are byte-identical
  to the canonical files.
- All 8,587 expressible SWU card identities canonicalize cleanly against the real catalog.

**Not verified at all:**

- **No Swift has ever been compiled.** There is no Swift toolchain in the cloud container. ~3,000
  lines across ten files are desk-checked only. Expect first-build errors.
- **No BLE code has been near a radio.** Advertising, connection handling, the long-read path, the
  notify race — none of it observed. Two physical devices are required; the simulator cannot do it.
- The conformance runner (`TradeProtocolConformance.runDebugCheck()`, 24 checks) has never been
  executed. It is deliberately **not** wired into startup — call it from a debug menu or a
  breakpoint.

## Next steps, roughly in value order

1. **Build it.** Highest value by a wide margin. Then run `runDebugCheck()`. If a vector fails, the
   vectors are the published contract — the Swift is what changed.
2. **Hardware bring-up on two devices.** The one open question the design cannot answer on its own:
   whether an iOS peripheral's filter-carrying second service UUID is visible to a scanner (iOS and
   Android both) rather than landing in Apple's overflow area. §3.2 and §9. Correctness does not
   depend on it — `WantFilter` covers the negative case — but scan cost in a busy room does.
3. **Trade UI.** Nothing exists. `TradeSession` publishes `matches`, `isRunning` and
   `wantListProblem` and is ready to bind to.
4. **Wire it into the app.** No entry point, no settings toggle, no entitlement gating.
   `TradeSession.wantListChanged()` is never called by anything.

## Known gaps and deliberate exclusions

- Tokens are out of scope by decision (§1, §4.4.3) — `collectorNumber` is an integer, which
  excludes them by construction.
- The SOR and SHD printed-total-02 promo runs are absent from `sets`, so their cards cannot be
  traded. Holding position pending real data, not a ruling. See §4.6.1.
- **A real card-identity collision exists and is not registered.** Two SHD promos from different
  programmes share a set code and collector number. `knownCollisions` is the mechanism; the blocker
  is that nobody has established how to tell the two cards apart physically, and §4.6 requires that.
  Registering it with a nominal description would pass every check and be worthless at the table.
- 8 of 28 print runs are `verified: false` — wording unconfirmed against `canonicalSource`, not
  substance. The canonical source is partly wrong and partly missing here.
- The app's `cardId` has no equivalent of `variantKey`, so it inherits the collision problem with
  no way to express it. That is an app-side design question, not a protocol one.

## Things that will bite if forgotten

- `cardId` alone **cannot** produce a protocol identity. For a mainline card of a shared set code
  the printed total lives in the catalog (`CardInfo.cardCount`), not in the key. The mapping needs
  the catalog.
- Never derive a foil identity by flipping `finish` on a standard card's number. From JTL onward the
  foil printing has its own collector number (§4.4.1). The wrong identity hashes cleanly and matches
  nothing.
- Every failure mode in this protocol is **silent**. There is no error to see and no bug report to
  receive — only an absence of matches. That is why the vectors exist and why they must not be
  edited to make code pass.
