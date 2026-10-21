# Trade protocol — working status

Where this stands, what is actually verified, and what to pick up next. Written to survive the
session it was produced in; the *reasoning* behind each decision is in `SPEC.md` and in the commit
messages, which are deliberately long for that reason.

Last updated on `trade/definition-v6` after the trade fixes and trade builder groundwork (October 2026).

## Negotiation feature (October 2026)

New optional feature `negotiation`, format 1 (SPEC §6.5): structured trade suggestions between two
matched peers over a new `Negotiation` characteristic (`a2ec42c3-94c1-594e-9297-00f4778f2915`,
derived like the others), declared in `Info.features`. Lines carry an optional condition byte.
Nothing in §§3–6.4 changed on the wire; `swu-v1.json` is byte-identical. `reference.py` implements
it, `vectors/negotiation-v1.json` pins it down, `make_vectors.py --check` covers both files and
replays the scenarios. `finishes` is now append-only (§4.2, §4.6 rule 6; `validate.py --base`).
The repository is self-contained: the vectors build from `testdata/swu-catalog-sample.json`, not
from an app's catalog, and `check_app_bundle.py` takes the app path as an argument. Not yet
implemented in any app; no transport of it has been near a radio.

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
3. ~~**Trade UI.**~~ Done: `TradeView` (card art and names, nearby players, "N for M", Pause,
   the want picker) and the DEBUG-only `TradeDebugView`.
4. ~~**Wire it into the app.**~~ Done: Trade opens from the Home screen and from the Collection
   tab's toolbar. It is free (no entitlement) and has no settings toggle, on purpose — see
   TradeView.swift's header. `TradeSession.wantListChanged()` is called by TradeView's debounced
   binder subscription and when the want picker saves.

Added by the trade fixes work (October 2026), all local to one phone — the protocol, SPEC and
vectors are unchanged:

- Matches resolve against our own lists only: what a peer wants from us against Have, what we
  want against Want, with the foil flag kept (`TradeMatchCard`).
- Confirmations are intersected with the hashes actually sent; the peripheral ignores peers that
  sent no Info; only acceptable filters are cached.
- `TradeInventory` caches its hash tables until `invalidate()`, and the trade name is read live.
- The 24-card cap is re-checked on every want-list change: trading suspends when over and resumes
  when back under, unless the user paused.
- Bluetooth state is reported by both services and shown (`TradeRadioState`, `TradeSession.radio`)
  instead of "Searching" forever.
- One status decision for headline, badge, spinner and VoiceOver (`TradeStatusPresentation`);
  status changes are spoken; the want picker summarises chosen and not-offered cards and asks
  before offering none (`TradeWantSelectionSummary`).
- Groundwork for building and completing a trade on one phone, with history and undo:
  `TradeProposal`, `TradeCompletionPlan`, `TradeHistory`, `ItemValuation`, `BinderStore+Trade`.
  Their screens are not built yet.
- Match expiry rules exist (`TradeMatchRules`) but are not yet used by `TradeSession`.
- Unit tests: `SWU scanTests` (Xcode) and the Foundation-only subset in `tools/linux_tests`.

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
