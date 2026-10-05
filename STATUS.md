# Trade protocol — working status

Where this stands, what is actually verified, and what to pick up next. Written to survive the
session it was produced in; the *reasoning* behind each decision is in `SPEC.md` and in the commit
messages, which are deliberately long for that reason.

Last updated at `trade-protocol/implementation` head "Add the BLE transport layer".

## Definition version 6

`definitions/swu.json` is at `definitionVersion` 6. It adds HMW and HMWP (releaseOrder 9, distinct
numbering) and CST (releaseOrder 9, shared, `verified: false`, one run so no printedTotal). SOR-2 and
SHD-2 are still absent. The app's `finish_map.json` was replaced at the same time (HMW, HMWP, CST and
more P26 entries); existing entries are unchanged, and the 22 new P26 entries for cards that were
already tradeable (P26/122, P26/216–236) are `null`, so they keep hashing as `standard` exactly as
in version 5.

Checked with the mapping oracle over the whole app catalog, v5 against v6: the only changed lines
are 1,972 that went from `notCanonical` to `ok` (HMW 1,888, HMWP 80, CST 4). No line that was
already `ok` changed its hash. The vectors were regenerated (they were already stale against the
catalog) and gained three encoding vectors (HMW 610 foil, HMWP 25 foil, CST 2 foil). The iOS
in-app conformance run (`runDebugCheck()`) has not been rerun against v6; that needs an Xcode build.

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
- All 9,586 expressible SWU card identities canonicalize cleanly against the real catalog
  (definitionVersion 6).

**Verified on the Mac (Xcode 27.0, iPhone 17 simulator, iOS 26.4.1):**

- The Swift now compiles. The first-ever compile of the ~3,000 lines across ten files produced a
  single error (`TradeWire.swift`, an unqualified static member), since fixed.
- `TradeProtocolConformance.runDebugCheck()` ran and passed: 104 checks, no failures. 87 of them
  are checks against the published vectors; the other 17 are implementation checks (session-id
  decoding and encoding in `Info`, and `TradePeerLedger`) that pin down this app's own behaviour,
  are NOT vectors, and do not read the vector file. The run
  needed a temporary call from app launch (not committed) and a command-line
  `IPHONEOS_DEPLOYMENT_TARGET=26.4` override, because the installed simulators are older than the
  project's 26.5 deployment target.

**Not verified at all:**

- **No BLE code has been near a radio.** Advertising, connection handling, the long-read path, the
  write-then-read ordering of steps 3–4, the random connect delay and the cross-role session-id
  recognition (§6.1, §6.3) — none of it observed. Compiling says nothing about
  behaviour here. Two physical devices are required; the simulator cannot do it.
- The conformance runner is deliberately **not** wired into startup — call it from a debug menu or
  a breakpoint.

## Next steps, roughly in value order

1. ~~**Build it.**~~ Done: it builds and `runDebugCheck()` passes (see above). If a vector ever
   fails, the vectors are the published contract — the Swift is what changed.
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
- 9 of 31 print runs are `verified: false` — wording unconfirmed against `canonicalSource`, not
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
