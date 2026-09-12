# Open TCG Trade Protocol — v1 (Draft)

A vendor-neutral, offline, peer-to-peer protocol for two people to discover that they can trade
physical trading-card-game cards, using nothing but Bluetooth Low Energy. Designed to be
implemented independently by apps on different platforms (this draft targets iOS and Android
specifically) and for different card games, without a central server, without user accounts, and
without either side ever transmitting their full collection.

This document is meant to be readable on its own, without the design conversation that produced
it. Companion files:

- [`schema/game-definition.schema.json`](schema/game-definition.schema.json) — the JSON Schema
  every per-game definition file must validate against.
- [`definitions/swu.json`](definitions/swu.json) — the definition file for *Star Wars: Unlimited*,
  serving as the worked example.

## 1. Scope

**In scope for v1:**
- Two people, physically near each other, both running a compatible app, discovering that one has
  cards the other wants (and vice versa).
- Works entirely offline — no internet connection required by either device.
- Works across platforms (an iOS app and an Android app implementing this spec can trade with each
  other) and across card games (a definition file, described in §4, is how a game plugs in).

**Explicitly out of scope for v1** — noted so implementers don't assume these are missing by
accident:
- **Any transport other than plain Bluetooth LE.** Platform-specific accelerants (Apple's
  MultipeerConnectivity, Android's Nearby Connections API) are a plausible future optimization for
  same-platform pairs, layered *on top of* this protocol, never a replacement for it — cross-platform
  interop always falls back to what's specified here.
- **Discovery when the app isn't open.** No background scanning/advertising, no push
  notifications, no server. A match is only found while both apps are active.
- **Chat or any messaging between matched users.** Out of scope by product decision, not a
  technical limitation — nothing here prevents adding it later.
- **Real identity.** Users are shown a self-chosen display handle, never a real name or account
  identity — there are no accounts.
- **A general internet/marketplace trading mode.** A fundamentally different feature (needs
  accounts, a backend, and a matching/trust model) — not addressed by this spec at all.

## 2. Terminology

- **Card identity** — the opaque value that identifies one specific, tradeable physical object
  (a specific printing and finish of a card). The protocol never interprets this value's structure;
  it only hashes and compares it. What actually constitutes a card's identity is defined per game
  (§4).
- **Definition file** — a JSON document, one per supported card game, declaring how that game's
  card identities are structured, its known sets, and its finish vocabulary.
- **Have list / Want list** — the sets of card identities a user is offering and seeking,
  respectively. Only Want lists are ever broadcast (§5); Have lists never leave the device that
  owns them.
- **Peer** — the other device in a given BLE exchange. Every device acts as both a BLE peripheral
  (advertising its own Want list) and a BLE central (scanning for others) simultaneously.

## 3. Transport layer

Plain Bluetooth Low Energy, using each platform's native APIs (Core Bluetooth on iOS, the
`android.bluetooth.le` APIs on Android). No other transport is used or required.

### 3.1 Service UUID

Each game gets its own BLE service UUID, **derived, not assigned by hand**: a UUID v5 (RFC 4122,
name-based, SHA-1) computed from one fixed namespace UUID defined by this protocol, and the game's
`game` identifier string from its definition file (e.g. `"swu"`).

```
serviceUUID(game) = UUIDv5(PROTOCOL_NAMESPACE_UUID, game)
```

`PROTOCOL_NAMESPACE_UUID` is a single UUID generated once for this protocol and fixed forever
(placeholder in this draft: `00000000-0000-5000-8000-000000000000` — **replace with a real
generated UUID before this spec is used for anything real**, then never change it).

This means:
- Any implementer can compute a game's service UUID unilaterally from its `game` string — no
  registry, no coordination, no risk of collision between unrelated games picking the same UUID by
  hand.
- BLE's native service-UUID scan filtering (handled by the OS/radio) does game-routing for free —
  a Magic app and a SWU app never see each other's advertisements at the API level, and no
  advertisement bytes are spent signaling which game this is.

### 3.2 Advertisement contents

- **Primary advertisement packet** (legacy BLE, ≤31 bytes): flags + the service UUID from §3.1.
  This alone consumes nearly the whole packet.
- **Scan response** (a second, separate ≤31-byte packet, requested by an active scanner): 1 byte
  protocol version, followed by the Bloom filter bytes (§5).

Do not rely on BLE 5 extended advertising being available or controllable — plan around the
legacy-size budget above, since not every platform's peripheral-role API exposes it to third-party
apps.

## 4. Card identity model

### 4.1 Why identity is opaque to the protocol

Different card games have different, sometimes idiosyncratic, ways of identifying a specific
printing (set codes, collector numbers, treatments, promo programs — see §4.4 for a real example
of how messy this gets even within one game's history). The protocol does not try to model this
generically. Instead, it treats a card identity as **a fixed set of named, typed fields**, and each
game supplies its own field list via a definition file. The protocol only ever hashes and compares
these field values — it never assigns meaning to them.

### 4.2 Definition files

A definition file is a JSON document validating against
[`schema/game-definition.schema.json`](schema/game-definition.schema.json). Required top-level
fields:

| Field | Purpose |
|---|---|
| `schemaVersion` | Which version of the meta-schema this file conforms to. |
| `game` | Stable lowercase identifier (e.g. `"swu"`), used to derive the service UUID (§3.1). |
| `displayName` | Human-readable game name. |
| `definitionVersion` | Increments on any change to this file. **Hard rule: an identifier already constructible under an older version must resolve identically under every later version.** Additions are fine; redefining or reusing an existing meaning is not. |
| `canonicalSource` | `{ name, url }` — the authoritative catalog this file defers to. Disputes about correctness are resolved by checking here, not by discussion. |
| `identifier.fields` | Ordered list of the named, typed fields that make up this game's card identity (see below). |
| `finishes` | A **closed vocabulary** of finish names (e.g. `["standard", "foil"]`) — not free text. If a value isn't listed, it isn't valid for this game. This exists specifically so two independent implementers can't quietly diverge on naming the same concept differently (e.g. "foil" vs. "holo"). |
| `sets` | Array of `{ code, name, releaseOrder }`. |

Optional: `knownCollisions` (§4.5).

### 4.3 Representing a card identity

A card identity is a **plain, flat JSON object** whose keys are the field names declared in
`identifier.fields`. There is no compact string encoding — an earlier draft of this spec tried to
define one and it was dropped deliberately, because it required inventing and documenting a bespoke
delimiter syntax purely to save characters, which actively worked against third-party readability
for no real benefit (nothing about matching or hashing requires a single-line string form).

```json
{ "setCode": "SHD", "collectorNumber": 7, "printedTotal": 20, "finish": "foil" }
```

```json
{ "setCode": "JTLP", "collectorNumber": 4, "finish": "foil" }
```

A field marked `"optional": true` in `identifier.fields` may be entirely absent from a given card's
object (as `printedTotal` is above) — never present with a null or placeholder value.

Each field declaration in `identifier.fields` has:

| Key | Meaning |
|---|---|
| `name` | Field name, used as the JSON key. |
| `type` | `"string"` or `"integer"`. |
| `case` | For strings: `"upper"`, `"lower"`, or `"asIs"` — the required normalization. |
| `zeroPad` | For integers: display-only zero-padding width (does not affect the numeric value or its hash encoding, see §5.2). |
| `optional` | Boolean, default `false`. |

### 4.4 Worked example: why SWU's identity looks the way it does

Included because the reasoning generalizes to other games with irregular printing history, and a
future contributor extending this spec to a new game should expect similarly messy cases, not be
surprised by them.

- **Treatments (Hyperspace, Showcase, etc.) are *not* identifier fields.** SWU's canonical source
  already assigns each treatment its own `collectorNumber`, so `setCode` + `collectorNumber` already
  disambiguates them. Treatment names are display metadata only, sourced from the canonical source,
  never matched on.
- **`finish` is always a required field, even though it's sometimes redundant.** Early SWU sets
  (Spark of Rebellion, Shadows of the Galaxy, Twilight of the Republic) gave a foil card the *same*
  `collectorNumber` as its non-foil counterpart — finish there is a pure flag. Later sets give foil
  printings their own distinct number. Rather than requiring every implementer to know, per set,
  which era's rule applies, `finish` is always present and explicit — redundant with the number in
  the later case, load-bearing in the earlier case, uniform either way.
- **`printedTotal` is optional, and *which* cards have it doesn't follow a simple "old sets vs. new
  sets" rule.** In SOR/SHD/TWI, weekly-play promo cards share their mainline set's code and *do*
  print a total (e.g. `07/20`), because the total is what disambiguates them from the mainline card
  sharing that number (`07/262`). From Jump to Lightspeed onward, weekly-play promos moved to their
  own dedicated set code (`JTLP`, `SECP`, `LAWP`, ...) — and once the set code itself disambiguates,
  the total is no longer printed at all. The field's optionality tracks exactly when it stops being
  load-bearing, not a fixed era cutoff.
- **Card name is deliberately *not* part of identity.** It's descriptive metadata, sourced from the
  canonical source at display time. Names can repeat across a game's history and can vary by print
  language; collector number within a set is already a stable, unambiguous locator and doesn't have
  either problem.

### 4.5 `knownCollisions` — last-resort disambiguation

Some vanishingly rare cases exist (or may someday be found) where every field above still doesn't
distinguish two real, distinct cards — e.g. two different promo programs that happen to have used
the same set code, number, and total. `identifier.fields` may include one additional optional field,
`variantKey`, used *only* for these registered exceptions:

```json
"knownCollisions": [
  {
    "matchFields": { "setCode": "SHD", "collectorNumber": 7, "printedTotal": 20 },
    "variants": [
      {
        "variantKey": "prerelease-judge",
        "description": "Distributed to Judges at pre-release events; carries a small 'Judge' wordmark near the bottom border."
      },
      {
        "variantKey": "store-showdown-top8",
        "description": "Awarded to Store Showdown Top 8 finishers; carries a Store Showdown foil stamp instead."
      }
    ]
  }
]
```

(The above is illustrative of the *shape* only — it is not a claim that this specific collision is
real. `knownCollisions` should start empty for a new game and grow only as genuine cases are found
and verified against the canonical source.)

Semantics: if a card's other fields match a registered `matchFields` entry, a complete identity for
that card **must** also carry one of the listed `variantKey` values. An identity that matches a
`matchFields` entry but carries no `variantKey` is under-specified, not a valid third possibility.

Three rules the JSON Schema in `schema/game-definition.schema.json` does not (and structurally
cannot) enforce on its own — a contribution-time validation script should check these on every pull
request, same as schema validation:
1. Every key in `matchFields` is a real field name from `identifier.fields`, and is not `variantKey`
   itself.
2. `variantKey` values within one entry's `variants` are distinct from each other.
3. No two `knownCollisions` entries register the same `matchFields` combination.

### 4.6 Extending or correcting a definition file

Definition files are meant to be community-maintained (e.g. via pull requests against a public
repository), reviewed and merged by a maintainer, and validated in CI against the meta-schema plus
the checks in §4.5. `canonicalSource` is the tiebreaker for factual disputes.

The meta-schema deliberately does **not** set `additionalProperties: false` anywhere. This is a
load-bearing choice, not an oversight: it means a future addition — a richer `knownCollisions`
shape, a concept nobody has needed yet — can appear in a real definition file without the schema
having had to predict it in advance, as long as `definitionVersion` still increments and nothing
already-assigned is redefined.

## 5. Discovery: the Want-list Bloom filter

### 5.1 What gets broadcast, and why

Each device broadcasts a **Bloom filter built from its own Want list** — never its Have list, and
never the raw list itself.

Two independent reasons converge on this shape:
- **Size.** A BLE advertisement's scan response has roughly 27–29 usable bytes after overhead
  (§3.2) — nowhere near enough for a literal list of card identities.
- **Which list to expose.** Broadcasting Want (not Have) means a peer can determine "do I have
  anything they want" using only its own private Have list plus the received filter — a device
  never needs to expose what it actually owns just to be discoverable.

**This is a bandwidth optimization, not a privacy mechanism, and should never be described as the
latter.** A Bloom filter's usual privacy value comes from the space of possible values being large
enough that brute-forcing it is impractical. Most TCGs have a small, fully enumerable card
catalog (SWU: on the order of a few thousand). Testing every real card in that catalog against a
broadcast filter and reconstructing most of the true Want list is computationally trivial. Treat the
filter purely as a bandwidth-and-efficiency device that avoids a full connect-and-exchange with
every stranger in the room.

### 5.2 Parameters and hash function

- **Filter size:** `m = 192` bits (24 bytes).
- **Hash functions:** `k = 4`, derived from a single 64-bit hash via double hashing
  (Kirsch–Mitzenmacher), not four independently-implemented hash functions.
- **Hash algorithm: 64-bit FNV-1a**, offset basis `14695981039346656037`, prime
  `1099511628211`, applied to the canonical byte encoding below. **Do not use a language's built-in
  general-purpose hash** (e.g. Swift's `Hashable`/`hashValue`, which is randomized per process by
  design) — it will not agree between platforms, or even between two runs of the same app.

```
hash64 = FNV1a_64(canonicalBytes)
h1 = hash64 >> 32            // upper 32 bits
h2 = hash64 & 0xFFFFFFFF     // lower 32 bits
for i in 0..<4:
    bitIndex[i] = (h1 + i * h2) mod 192
```

- **Canonical byte encoding**, for a card's identity object, used only for the purposes of this
  hash (this is *not* the JSON representation from §4.3): for each field in `identifier.fields`, in
  the order declared, emit:
  1. One presence byte — `0x01` if the field's value is present on this card, `0x00` if the field
     is `optional` and absent (every required field is always `0x01`).
  2. If present: the value's bytes — a string is case-normalized per its `case` rule then UTF-8
     encoded; an integer is encoded as 4 bytes, big-endian, unsigned (`zeroPad` is a display concern
     only and has no effect here).

  Uniform treatment (every field always gets a presence byte, required or not) keeps the encoder a
  single loop with no per-field branching on optionality.

These two parameters (`m`, `k`) are **protocol-level constants**, fixed because they answer a
question about the shared physical medium (BLE's advertisement size), not about any particular
game. How many cards a user is allowed to curate into their broadcast Want list is a separate,
app-level product decision (SWUScan's own UI enforces a cap and prompts the user to narrow a larger
binder down) — not something this protocol dictates.

## 6. Matching: connect and confirm

A Bloom filter hit is a candidate, not a confirmed match — false positives are the deliberate
tradeoff for the filter's small size. Confirming a candidate requires a short BLE connection.

### 6.1 Characteristics

| Characteristic | Direction | Contents |
|---|---|---|
| `Info` | peripheral → central (read) | protocol version, `game`, `definitionVersion`, peer's display handle |
| `PeerCandidates` | peripheral → central (read) | the peripheral's own candidate cards (its Have-cards that hit the central's advertised filter) |
| `MyCandidates` | central → peripheral (write) | the central's candidate cards (its Have-cards that hit the peripheral's advertised filter) |
| `Confirmed` | peripheral → central (notify) | which entries of `MyCandidates` are genuine matches against the peripheral's real Want list |

`PeerCandidates`, `MyCandidates`, and `Confirmed` all carry plain arrays of the same JSON card
identity objects defined in §4.3 — there is no separate, more compact wire format for this step.
Post-connection BLE MTU is generous (commonly 185+ bytes after negotiation), so the size pressure
that shaped the advertisement layer in §5 does not apply here.

### 6.2 Sequence

By the time two devices connect, **each has already independently scanned the other's advertised
filter** — so each already knows its own candidate list before the connection even opens. This
means only one direction actually needs confirming over the wire:

1. Central connects to peripheral.
2. Central reads `Info` and `PeerCandidates`.
3. Central checks `PeerCandidates` **locally**, against its own real Want list already on-device —
   no message needed for this half, since the central already holds both pieces of information.
4. Central writes `MyCandidates`.
5. Peripheral checks `MyCandidates` against its own real Want list and sends `Confirmed` via notify.
6. Central disconnects.

Do not hold the connection open past this exchange. Cache the confirmed results locally and
continue scanning — this keeps the number of simultaneous BLE connections small regardless of how
many candidates are nearby (see §7 for how a busy room is handled).

## 7. Proximity filtering and match ranking

### 7.1 RSSI is a pre-connect filter, not a ranking signal

When more Bloom-filter candidates are discovered than can reasonably be connected-and-confirmed
one at a time, use BLE RSSI (signal strength) to **trim the candidate pool** — drop the
weakest-signal candidates first, before attempting §6's connect/confirm sequence on the rest.

RSSI must **not** be used to rank or order the final list of confirmed matches shown to the user.
Trade quality (a real, confirmed measure, only known after §6 completes) and physical proximity
(a rough, noisy, pre-connect estimate) are different things, and conflating them can bury a
better match standing a few meters further away behind several weaker, closer ones.

### 7.2 RSSI is noisy — treat it as a trend, never a distance

Raw RSSI fluctuates significantly frame to frame even between two stationary devices, and is
measurably affected by bodies, walls, and reflective surfaces — none of which correlate cleanly
with actual distance. Any RSSI-derived signal (including the proximity-finding feature in §7.3)
must be smoothed over a rolling window and presented only as a **relative trend** ("getting
warmer/colder"), never as an absolute distance claim.

### 7.3 Optional: mutual-consent proximity finding

Once two users have a confirmed match, either side may request a live proximity indicator to help
physically locate each other. This must:
- Require **explicit acceptance from both sides**, shown with a warning, before any live signal is
  exchanged for that pairing.
- Be off by default and scoped to that one match — never a standing, always-on broadcast of a
  user's live proximity to anyone else.
- Use the smoothing rule in §7.2.

## 8. Non-goals recap

For a quick reference when reviewing an implementation against this spec, these are deliberate
absences, not gaps to fill in without a product decision first:
- No push notifications, no background operation.
- No chat.
- No accounts, no real identity — only a self-chosen display handle.
- No platform-specific transport (MultipeerConnectivity, Nearby Connections) in v1 — BLE only.
- No internet-based/marketplace trading mode.

## 9. Open items

Tracked here rather than silently assumed:
- `PROTOCOL_NAMESPACE_UUID` in §3.1 is a placeholder and must be replaced with a real, permanently
  fixed UUID before any implementation ships.
- The governance process for reviewing and merging definition-file contributions (who has merge
  rights, how disputes beyond what `canonicalSource` settles are resolved) is not yet defined.
- `knownCollisions`'s shape (§4.5) is a first draft; expect to revise it once real collisions are
  found in practice rather than anticipated in the abstract.
