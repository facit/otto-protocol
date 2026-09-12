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
- [`validate.py`](validate.py) — the contribution-time validator for the rules §4.6 lists as ones
  the JSON Schema structurally cannot enforce.

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
  respectively. Only Want lists are ever broadcast (§5). A Have list is never broadcast and is
  never sent in full; a bounded slice of one — the cards that hit a specific peer's filter, capped
  at `nMax` (§5.3) — is disclosed during a connection, and only reciprocally (§6.3).
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

`PROTOCOL_NAMESPACE_UUID` is fixed forever as:

```
8a44bf6a-7c5c-40fa-84d1-f3a5ec791831
```

For *Star Wars: Unlimited* (`game` = `"swu"`) this yields service UUID
`0a3edc52-5120-5164-972e-8dfddc85be41`. Implementers should hard-code the derived value for the
games they support and assert it against a runtime UUIDv5 computation in tests, rather than
deriving it on every launch.

The namespace UUID also carries the protocol's **major version**: a future revision that changes
the advertisement or characteristic layout incompatibly gets a *new* namespace UUID, and therefore
a wholly different service UUID per game. Two incompatible versions simply never see each other,
which is why no version byte is spent in the advertisement itself (the protocol version is still
reported in `Info`, §6.1, for diagnostics and for compatible minor revisions).

This means:
- Any implementer can compute a game's service UUID unilaterally from its `game` string — no
  registry, no coordination, no risk of collision between unrelated games picking the same UUID by
  hand.
- BLE's native service-UUID scan filtering (handled by the OS/radio) does game-routing for free —
  a Magic app and a SWU app never see each other's advertisements at the API level, and no
  advertisement bytes are spent signaling which game this is.

### 3.2 Advertisement contents

The advertisement's **only guaranteed content is the service UUID from §3.1**. Carrying the Bloom
filter (§5) in the advertisement is an *optional optimization* that implementations SHOULD attempt
and MUST NOT depend on; §6 works identically either way, via the `WantFilter` characteristic.

An earlier draft of this spec specified the filter as mandatory advertisement content — 1 version
byte plus 24 filter bytes in the scan response. That does not survive contact with either
platform's constraints, which is worth recording so nobody re-proposes it:

**The byte budget.** A legacy BLE advertising packet and its scan response carry 31 bytes of
payload each, and every AD structure inside costs 2 bytes of header (length + type):

| AD structure | Bytes | Fits in one 31-byte packet? |
|---|---|---|
| Flags | 3 | — |
| One 128-bit service UUID | 18 | yes, with flags (21 total) |
| Two 128-bit service UUIDs | 34 | **no** (37 with flags) |
| Service data, 128-bit UUID + 16 filter bytes | 34 | **no**, even alone in the scan response |
| Service data, 128-bit UUID + 24 filter bytes | 42 | **no**, by a wide margin |
| Manufacturer data (2-byte company ID) + 24 filter bytes | 28 | yes, in the scan response |

So 24 bytes of filter fits *only* as manufacturer-specific data — which requires a Bluetooth SIG
company identifier this protocol does not have, and which iOS cannot emit at all (below). A
128-bit service UUID is the one container that both platforms can advertise and that leaves room
for the game UUID alongside it, and it holds exactly **16 bytes**. That is where `m = 128` in §5.2
comes from: the filter is sized to the largest container the medium actually offers, not chosen
first and fitted afterwards.

**The iOS constraint.** `CBPeripheralManager.startAdvertising(_:)` accepts only two keys —
`CBAdvertisementDataLocalNameKey` and `CBAdvertisementDataServiceUUIDsKey`. There is no supported
way for a third-party iOS app to place arbitrary bytes into an advertisement: no service data, no
manufacturer data, and no control over how Core Bluetooth packs what it is given across the primary
packet and the scan response. iOS also silently relocates service UUIDs it cannot fit into an
Apple-proprietary "overflow area" that only other iOS devices can scan, and only when scanning for
that exact UUID — invisible to Android, so a design that relies on it is not cross-platform.

**What implementations do, therefore:**

- **Always** advertise the §3.1 service UUID. This is what makes a peer discoverable and is
  sufficient for a correct implementation.
- **Optionally** additionally advertise the 16-byte filter as a second 128-bit service UUID (its
  128 bits being the filter, verbatim, big-endian). A scanner identifies it by elimination: any
  advertised 128-bit UUID other than a game service UUID it recognizes is a filter payload. This is
  the only optional path both platforms can emit, and per the table it cannot share a packet with
  the game UUID and flags — it relies on the stack placing it in the scan response, which is the
  open hardware question in §9. Note that **service data is not an alternative**: with a 128-bit
  UUID it is 34 bytes and does not fit even alone in a scan response. Manufacturer data does fit,
  but needs a Bluetooth SIG company identifier, so it is available only to an implementer who has
  one and is not interoperable without registering it here first.
- **Never** require it. A peer advertising the service UUID with no recognizable filter payload is
  a normal, fully supported peer: the scanner connects and reads `WantFilter` (§6.1) to obtain the
  same 16 bytes, then proceeds exactly as if it had read them from the air. Treat this as the
  expected path, not the degraded one — it is what every iOS peripheral will do if the question in
  §9 resolves badly.

Because the iOS-to-iOS case will in practice fall back to connecting, implementations SHOULD cache
`peer -> filter` keyed by the platform's peer identifier and re-read only after a cache lifetime of
their choosing, so a stationary pair of phones does not reconnect on every scan callback.

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
| `sets` | One entry per **print run**, `{ code, name, releaseOrder, finishNumbering, printedTotal? }`. Several entries may share a `code` when one printed set code covers more than one run; `printedTotal` is then required on each and is what tells them apart (§4.4.2). `finishNumbering` declares whether that run numbers foils separately (§4.4.1). Not merely descriptive — §4.5 makes it load-bearing for canonicalization. |

Optional: `knownCollisions` (§4.6).

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

The first is a weekly-play promo from a set code shared with its mainline set, so it carries the
total that tells the two apart; the second is from a code used by exactly one print run, so it does
not. That difference is not stylistic — §4.4.2 makes it a rule, and §4.5 makes violating it invalid.

A field marked `"optional": true` in `identifier.fields` may be entirely absent from a given card's
object (as `printedTotal` is above) — never present with a null or placeholder value. `finish`
being present in both examples does **not** mean a foil identity can be formed by taking a standard
card and flipping the field; read §4.4.1 before writing any code that constructs one.

Each field declaration in `identifier.fields` has:

| Key | Meaning |
|---|---|
| `name` | Field name, used as the JSON key. |
| `type` | `"string"` or `"integer"`. |
| `case` | For strings: `"upper"`, `"lower"`, or `"asIs"` — the required normalization. |
| `zeroPad` | For integers: display-only zero-padding width (does not affect the numeric value or its hash encoding, see §5.5). |
| `optional` | Boolean, default `false`. |

### 4.4 Worked example: why SWU's identity looks the way it does

Included because the reasoning generalizes to other games with irregular printing history, and a
future contributor extending this spec to a new game should expect similarly messy cases, not be
surprised by them.

- **Treatments (Hyperspace, Showcase, Prestige, etc.) are *not* identifier fields.** SWU's canonical
  source already assigns each treatment its own `collectorNumber`, so `setCode` + `collectorNumber`
  already disambiguates them. Treatment names are display metadata only, sourced from the canonical
  source, never matched on.
- **Card name is deliberately *not* part of identity.** It's descriptive metadata, sourced from the
  canonical source at display time. Names can repeat across a game's history and can vary by print
  language; collector number within a set is already a stable, unambiguous locator and doesn't have
  either problem.
- **`finish` is always a required field, but what it *means* changes between sets** — and that
  change is the single most dangerous thing in this spec to get wrong. See §4.4.1.
- **`printedTotal` is present only when a set code is shared by more than one print run.** See
  §4.4.2.

#### 4.4.1 The two finish-numbering eras, and the interop trap between them

SWU numbers foils two different ways depending on the set:

- **Shared numbering** (Spark of Rebellion, Shadows of the Galaxy, Twilight of the Republic): a
  foil card carries the *same* `collectorNumber` as its non-foil counterpart. `finish` is a pure
  flag, and it is the only thing distinguishing the two.
- **Distinct numbering** (Jump to Lightspeed onward): the foil printing is its own entry in the
  canonical source with its *own* `collectorNumber`. The standard card and its foil twin are two
  different numbers.

An earlier draft kept `finish` required in both eras on the reasoning that this way "no implementer
needs to know per-set which numbering rule applies". **That reasoning is wrong, and inverted.** An
implementer must know the rule regardless, because in the distinct-numbering era emitting the right
identity means emitting the *foil printing's own number*. Keeping `finish` uniformly present does
not remove that requirement — it adds a second, wrong way to express the same card:

```json
{ "setCode": "JTL", "collectorNumber": 100, "finish": "foil" }
```

If `JTL/100` is a standard-numbered card whose foil twin is a different number, the object above
describes no real card at all. It is nevertheless perfectly well-formed, hashes cleanly, and goes
into a Bloom filter without complaint. An implementation that models foil as a flag on the base
card will produce it; a correct implementation will produce the twin's number instead. The two
never match, the trade is never discovered, and **nothing anywhere reports an error** — the only
symptom is an absence.

The fix is not to drop `finish` (it is genuinely load-bearing in the shared-numbering era) but to
make the era *declared data rather than implementer folklore*, and to make identities that
contradict it invalid rather than merely unlucky. Each entry in `sets` therefore declares
`finishNumbering`, and §4.5 gives the canonicalization and validation rules that follow from it.

#### 4.4.2 When `printedTotal` is present

`printedTotal` is a **disambiguator, not a description**. The rule is mechanical and derivable from
the definition file alone:

> `printedTotal` MUST be present if and only if this card's `setCode` appears on more than one
> entry in `sets`, and its value MUST equal that entry's declared `printedTotal`.

In SOR/SHD/TWI the printed set code is reused by several distinct print runs — the mainline set
plus weekly-play promos, token sheets and a small prerelease run — which share a code and are told
apart only by the total (`07/20` vs. `07/262`). Those codes appear on several `sets` entries, so
every card in them carries `printedTotal`. From Jump to Lightspeed onward weekly-play promos moved
to their own dedicated set codes (`JTLP`, `SECP`, `LAWP`, …); each code then belongs to exactly one
print run, appears once in `sets`, and no card in it carries `printedTotal`.

The previous draft described this correctly in prose but left it as a judgement call ("the field's
optionality tracks exactly when it stops being load-bearing"). Two implementers reading that can
reasonably disagree about whether a mainline SOR card carries `252` — and if they disagree, the
canonical encodings differ, the hashes differ, and the match is silently lost exactly as in §4.4.1.
Tying it to a countable property of the definition file removes the judgement entirely.

### 4.5 Canonicalization and validation

An identity object MUST be canonicalized before it is compared, hashed (§5.5), or transmitted. An
implementation MUST reject a non-canonical identity rather than repair it silently, because every
failure in this area is an invisible non-match rather than a visible error.

Canonical form requires all of:

1. **Field presence.** Every non-optional field in `identifier.fields` is present. Every optional
   field is either absent entirely or present with a real value — never `null`, never a placeholder.
2. **Case.** Every string field is normalized per its declared `case` rule.
3. **Key set.** No keys beyond those declared in `identifier.fields`.
4. **Set membership.** `setCode` matches the `code` of at least one entry in `sets`. Unlike earlier
   drafts, where `sets` was purely descriptive metadata, rules 5 and 6 make it load-bearing: a card
   whose set is not declared cannot be canonicalized at all.
5. **`printedTotal`.** Present exactly under the condition in §4.4.2, with the value that entry
   declares. Its presence or absence selects *which* `sets` entry this card belongs to when a code
   is shared.
6. **`finish` versus numbering.** Determined by the selected entry's `finishNumbering`:
   - `"shared"` — both finishes are valid against the same `collectorNumber`; `finish` is what
     distinguishes them, and any value from `finishes` is acceptable.
   - `"distinct"` — the `collectorNumber` already determines the finish. An implementation that can
     consult the canonical source's catalog MUST verify that `finish` agrees with what that number
     actually is, and reject the identity if it does not. An implementation that cannot (it ships no
     catalog) MUST still never *derive* a foil identity by flipping `finish` on a standard card's
     number; the foil's own number is the only correct source.
7. **`variantKey`.** Present if and only if §4.6 registers a collision this card matches.

A conforming implementation should have exactly one function that produces canonical identities and
no other path that constructs them, so rule 6 in particular cannot be bypassed by a convenience
initializer somewhere else in the codebase.

### 4.6 `knownCollisions` — last-resort disambiguation

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

Rules the JSON Schema in `schema/game-definition.schema.json` does not (and structurally cannot)
enforce on its own — a contribution-time validation script should check these on every pull
request, same as schema validation:
1. Every key in `matchFields` is a real field name from `identifier.fields`, and is not `variantKey`
   itself.
2. `variantKey` values within one entry's `variants` are distinct from each other.
3. No two `knownCollisions` entries register the same `matchFields` combination.
4. Within one `code`, either every entry in `sets` declares a `printedTotal` or none does — a code
   shared by several print runs where only some carry a total cannot be canonicalized (§4.4.2).
5. No two `sets` entries share both a `code` and a `printedTotal`, since nothing would then
   distinguish them.
6. `definitionVersion` has increased relative to the file being replaced, and no existing
   `sets` entry's `printedTotal` or `finishNumbering` has changed value — either would silently
   re-point identities that already exist on users' devices (§4.2's hard rule).

### 4.7 Extending or correcting a definition file

Definition files are meant to be community-maintained (e.g. via pull requests against a public
repository), reviewed and merged by a maintainer, and validated in CI against the meta-schema plus
the cross-referential checks in §4.6. [`validate.py`](validate.py) implements those checks and is
the script referred to throughout this section; run it on every pull request that touches
`definitions/`. It deliberately does not reimplement JSON Schema validation (use a real validator
alongside it), and it cannot check rule 6, which compares a file against its own previous revision
— that one belongs in the CI job that knows the base revision.

`canonicalSource` is the tiebreaker for factual disputes.

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

- **Filter size:** `m = 128` bits (16 bytes) — the largest payload the advertisement layer can
  actually carry on both platforms; see the container table in §3.2 for the derivation.
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
    bitIndex[i] = (h1 + i * h2) mod 128
```

Bit `j` of the filter is byte `j / 8`, bit `j % 8` counting from the most significant bit, so the
16-byte array is read and written as one big-endian 128-bit quantity on every platform.

### 5.3 Want-list size cap

**`nMax = 24`.** A device MUST NOT insert more than 24 distinct card identities into its broadcast
filter, and MUST truncate (by whatever selection its UI offers the user) rather than exceed it.

Earlier drafts left this to each app as a product decision. It isn't one: the filter's usefulness
collapses as `n` grows, and a peer running an uncapped implementation degrades *everyone else's*
scanning, because every one of its false positives costs some other device a wasted connection.
With `m = 128, k = 4`:

| Want-list size `n` | False-positive rate |
|---|---|
| 8 | 0.24% |
| 12 | 0.96% |
| 16 | 2.40% |
| 20 | 4.66% |
| 24 | 7.75% |
| 32 | 15.97% |
| 40 | 25.92% |
| 60 | 51.38% |

At `n = 24` the optimal `k` for this `m` is 3.70, so `k = 4` is the correct choice at the cap and
stays near-optimal across the whole usable range. Past ~40 the filter stops discriminating at all
and every peer in the room becomes a candidate.

How a user curates *which* 24 cards is still entirely an app concern (SWUScan's own UI prompts the
user to narrow a larger binder down); the ceiling is not.

### 5.4 Rejecting malformed and hostile filters

A scanner MUST ignore a received filter with **more than 96 of its 128 bits set**.

A legitimately-built filter at the `nMax = 24` cap sets about 68 bits (53%); 96 is far above
anything honest construction produces, and the threshold costs a legitimate peer nothing. The check
exists because a filter with every bit set matches every card, which would otherwise make each
of the scanner's own Have-cards a candidate — see §6.3 for why that matters and for the reciprocity
rule that is the real defense.

### 5.5 Canonical byte encoding

For a card's identity object, used only for the purposes of the hash above (this is *not* the JSON
representation from §4.3): for each field in `identifier.fields`, in the order declared, emit:

1. One presence byte — `0x01` if the field's value is present on this card, `0x00` if the field
   is `optional` and absent (every required field is always `0x01`).
2. If present: the value's bytes — a string is case-normalized per its `case` rule then UTF-8
   encoded; an integer is encoded as 4 bytes, big-endian, unsigned (`zeroPad` is a display concern
   only and has no effect here).

Uniform treatment (every field always gets a presence byte, required or not) keeps the encoder a
single loop with no per-field branching on optionality.

The identity MUST be canonicalized per §4.5 before it is encoded. Hashing a non-canonical identity
produces a filter that silently fails to match the same physical card held by a correct
implementation — the failure mode is an absent match, with nothing anywhere reporting an error.

`m` and `k` are **protocol-level constants**, fixed because they answer a question about the shared
physical medium (§3.2's byte budget), not about any particular game. A definition file cannot
override them.

## 6. Matching: connect and confirm

A Bloom filter hit is a candidate, not a confirmed match — false positives are the deliberate
tradeoff for the filter's small size. Confirming a candidate requires a short BLE connection.

### 6.1 Characteristics

| Characteristic | Direction | Contents |
|---|---|---|
| `Info` | peripheral → central (read) | protocol version, `game`, `definitionVersion`, peer's display handle |
| `WantFilter` | peripheral → central (read) | the peripheral's 16-byte Want-list Bloom filter (§5) |
| `MyCandidates` | central → peripheral (write) | an object: the central's own 16-byte Want filter, plus its candidate cards (its Have-cards that hit the peripheral's filter) |
| `PeerCandidates` | peripheral → central (read) | the peripheral's own candidate cards (its Have-cards that hit the central's filter) |
| `Confirmed` | peripheral → central (notify) | which entries of `MyCandidates` are genuine matches against the peripheral's real Want list |
| `PeerConfirmed` | central → peripheral (write) | which entries of `PeerCandidates` are genuine matches against the central's real Want list |

`PeerCandidates`, `Confirmed` and `PeerConfirmed` carry plain arrays of the same JSON card identity
objects defined in §4.3 — there is no separate, more compact wire format for this step.
Post-connection BLE MTU is generous (commonly 185+ bytes after negotiation), so the size pressure
that shaped the advertisement layer in §3.2 does not apply here.

`MyCandidates` is the one exception, an object rather than a bare array:

```json
{ "filter": "<the central's 16-byte Want filter, base64>", "candidates": [ /* identity objects */ ] }
```

The filter has to travel with it. The peripheral cannot produce `PeerCandidates` — *its* Have-cards
that hit the *central's* filter — without holding the central's filter, and nothing guarantees the
peripheral ever scanned that peer's advertisement: both roles run simultaneously, but which peers
each role has seen is independent. Carrying it in the same write also makes the §6.3 gate atomic,
since a peer cannot obtain `PeerCandidates` without having disclosed both its filter and its
candidates in one operation.

`WantFilter` is the mirror of that field, for the other direction: it exists so a peer whose
platform cannot advertise its filter (§3.2 — in practice every iOS peripheral) is still fully
discoverable. A central that already read a filter from the advertisement MAY skip this read.

A device MUST NOT send more than `nMax` (§5.3) entries in any single candidate or confirmation
list.

### 6.2 Sequence

By the time two devices connect, each has (or can cheaply obtain) the other's filter, and each has
already computed its own candidate list. Both directions are then resolved in one connection:

1. Central connects to peripheral.
2. Central reads `Info`, and `WantFilter` if it did not get the filter from the advertisement.
3. Central writes `MyCandidates` — its own Want filter, and its Have-cards that hit the
   peripheral's filter.
4. Peripheral checks `MyCandidates` against its own real Want list and notifies `Confirmed`.
   *The central now knows what it holds that the peripheral genuinely wants.*
5. Central reads `PeerCandidates` — the peripheral's Have-cards that hit the central's filter,
   which the peripheral can now compute from the filter supplied in step 3. The peripheral MUST
   refuse this read (or return an empty array) until step 3 has occurred; see §6.3.
6. Central checks `PeerCandidates` **locally**, against its own real Want list already on device,
   and writes the surviving entries back as `PeerConfirmed`.
   *The peripheral now knows what it holds that the central genuinely wants.*
7. Central disconnects.

Step 6 is what makes one connection sufficient. An earlier draft stopped at step 5 on the reasoning
that "each has already independently scanned the other's filter, so only one direction needs
confirming over the wire". That is true of the *central* — which ends the exchange knowing both
halves — but leaves the **peripheral knowing nothing confirmed at all**, unable to show its own user
the match. The peripheral would learn of it only when it re-ran the entire exchange in the central
role against the same peer, so the supposed saving was a duplicated connection, not a halved one.

Do not hold the connection open past this exchange. Cache the confirmed results locally and
continue scanning — this keeps the number of simultaneous BLE connections small regardless of how
many candidates are nearby (see §7 for how a busy room is handled).

### 6.3 Reciprocity: why `PeerCandidates` is gated

`PeerCandidates` is the one place where any part of a Have list leaves its device (§2), so its
exposure has to be bounded deliberately.

Without the step-3 precondition, any device could connect to a peripheral and read that subset
having disclosed nothing itself (and, since step 3 now also carries the central's filter, there
would additionally be nothing for the peripheral to compute the subset *from*). Combined with a filter crafted so that every card hits it, a
single passive attacker could walk a room and harvest every participant's complete inventory —
while remaining, by construction, a peer with nothing to offer. §5.4's bit-count ceiling makes the
crafted filter harder to build but does not prevent it (an attacker can simply insert real cards
from the game's catalog, which is small and fully enumerable — §5.1).

The gate therefore makes exposure *symmetric and simultaneous*: a peer learns a bounded slice of
your Have list only after committing an equally bounded slice of its own, and both slices are
capped at `nMax`. An attacker still learns up to `nMax` of your cards per exchange, and must give
up `nMax` plausible cards of its own to do it. This is a cost imposed on harvesting, not a
prevention of it.

**Do not describe this as making Have lists private.** As with the Bloom filter in §5.1, the
honest claim is bounded, reciprocal disclosure of a small slice — not confidentiality. Any product
copy promising more than that is wrong.

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
- **The definition file for SWU is incomplete and partly unverified.** `definitions/swu.json` now
  enumerates print runs derived from a real catalog, but several set codes in it are an
  application's internal naming rather than confirmed printed codes, and are flagged as such in the
  file's own `notes`. Each needs checking against `canonicalSource` before it can be treated as
  authoritative. Until then, cards in those sets may fail rule 4 of §4.5 on a peer using a
  differently-named definition file.
- **Whether a scanner can read a second 128-bit service UUID from an iOS peripheral, on both iOS
  and Android, is unverified on hardware** (§3.2). The protocol is designed so the answer does not
  affect correctness — `WantFilter` covers the negative case — but it determines whether iOS peers
  are discoverable without a connection, which is a significant difference in scan cost and battery
  in a busy room. Measure this before optimizing anything else in the transport.
- The governance process for reviewing and merging definition-file contributions (who has merge
  rights, how disputes beyond what `canonicalSource` settles are resolved) is not yet defined.
- `knownCollisions`'s shape (§4.6) is a first draft; expect to revise it once real collisions are
  found in practice rather than anticipated in the abstract.
- `nMax` (§5.3) is derived from the filter's false-positive curve alone. Whether 24 cards is enough
  for the *product* — whether users routinely want to advertise more and find the cap frustrating —
  is a question for real use, and the answer may argue for a different discovery shape rather than
  a bigger filter, since the filter cannot grow past the §3.2 byte budget.
- Nothing in this spec authenticates a peer or binds a display handle to anything. A peer can claim
  any handle, and can advertise a Want list it has no intention of honoring. This is acceptable for
  a protocol whose output is "go talk to that person", but it must not be extended toward anything
  resembling an unattended or committed trade without revisiting it.
