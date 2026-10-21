# OTTO — Open TCG Trading Over-the-air — v1 (Draft)

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
- [`reference.py`](reference.py) — an executable reference implementation of §4.5, §5.2 and §5.5,
  and [`vectors/swu-v1.json`](vectors/swu-v1.json), the conformance vectors generated from it. An
  implementation that reproduces those bytes agrees with this document about the parts where
  disagreement would otherwise be invisible. `reference.py` also implements the optional trade
  negotiation feature (§6.5), whose vectors are
  [`vectors/negotiation-v1.json`](vectors/negotiation-v1.json).
- [`README.md`](README.md) — orientation for a third-party implementer.

## 1. Scope

**In scope for v1:**
- Two people, physically near each other, both running a compatible app, discovering that one has
  cards the other wants (and vice versa).
- Works entirely offline — no internet connection required by either device.
- Works across platforms (an iOS app and an Android app implementing this spec can trade with each
  other) and across card games (a definition file, described in §4, is how a game plugs in).
- Optionally (§6.5), two matched people agreeing on a concrete trade from their phones: a structured
  suggestion of cards to give and get, which the other side can accept, decline or change.

**Explicitly out of scope for v1** — noted so implementers don't assume these are missing by
accident:
- **Any transport other than plain Bluetooth LE.** Platform-specific accelerants (Apple's
  MultipeerConnectivity, Android's Nearby Connections API) are a plausible future optimization for
  same-platform pairs, layered *on top of* this protocol, never a replacement for it — cross-platform
  interop always falls back to what's specified here.
- **Discovery when the app isn't open.** No background scanning/advertising, no push
  notifications, no server. A match is only found while both apps are active.
- **Chat or any messaging between matched users.** Out of scope by product decision, not a
  technical limitation — nothing here prevents adding it later. The optional negotiation feature
  (§6.5) does not change this: it carries structured trade suggestions (card hashes, quantities and
  an optional condition grade) and their answers, never free text.
- **Real identity.** Users are shown a self-chosen display handle, never a real name or account
  identity — there are no accounts.
- **A general internet/marketplace trading mode.** A fundamentally different feature (needs
  accounts, a backend, and a matching/trust model) — not addressed by this spec at all.
- **Cards nobody trades.** A game's definition file decides what is expressible, and it is
  legitimate for it to exclude filler that is not meaningfully traded — tokens, for instance, which
  are not deck-legal and are supplied free with product. `definitions/swu.json` does exactly that;
  see §4.4.3. This is a per-game decision, not a protocol-level one: nothing here prevents a game
  whose tokens *are* collected from expressing them.

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
| `finishes` | A **closed vocabulary** of finish names (e.g. `["standard", "foil"]`) — not free text. If a value isn't listed, it isn't valid for this game. This exists specifically so two independent implementers can't quietly diverge on naming the same concept differently (e.g. "foil" vs. "holo"). **Append-only:** the negotiation feature sends a finish as its index in this array (§6.5.4), so a later version may add values at the end but never remove, reorder or rename one (§4.6 rule 6). |
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

#### 4.4.3 Tokens, and using a field's type as a scope boundary

SWU prints tokens with collector numbers like `T001`, which are not integers. `swu.json` declares
`collectorNumber` as `"type": "integer"`, so those cards have no canonical form and cannot be
traded — and that is deliberate. Tokens are not deck-legal and come free with product; excluding
them costs nothing real.

What is worth noticing is *how* they are excluded. The integer type does it by construction: there
is no rule for an implementer to remember, no list of excluded cards to keep in sync, and no way to
accidentally admit one. The alternative — declaring `collectorNumber` a string so every printed
number is expressible — would make zero-padding significant, so `"007"` and `"7"` would hash
differently and two implementations could silently fail to match on an ordinary card. Trading a
whole class of silent divergence for 56 cards nobody trades is a bad exchange.

A game whose tokens genuinely are collected can declare the field a string; it then owns that
padding hazard and must specify the exact printed form in its own notes.

Three SWU print runs (the printed-total-04 sheets under SOR, SHD and TWI) were tokens in their
entirety and are therefore absent from `sets` altogether. Tokens inside an otherwise tradeable set
need no special handling — they simply fail canonicalization like any other malformed identity.

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

(The above is illustrative of the *shape* only — the field values are invented. But the situation
it describes is not hypothetical: see §4.6.1.)

`knownCollisions` should start empty for a new game and grow only as genuine cases are found and
verified against the canonical source.

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
   re-point identities that already exist on users' devices (§4.2's hard rule). `finishes` is
   append-only: the previous revision's array is a prefix of the new one, because its indices are
   on the wire (§6.5.4).

#### 4.6.1 A real collision, and why it is not registered yet

This mechanism was drafted defensively, against collisions nobody had found. One exists.

Two *Star Wars: Unlimited* promos from different programmes — a prerelease promo and a Store
Showdown Top 8 promo — are printed with an identical set code **and** an identical collector
number. Every field in §4.3 gives the same value for both, so they are one identity as far as this
spec is concerned, and a user who holds one cannot express which.

It is nevertheless **not** registered in `definitions/swu.json`, and the reason is worth stating
because it constrains how this mechanism can be used. §4.6 requires each variant's `description` to
say how a person can physically tell it apart — a rule that exists so a matched user can actually
check what they are holding. Nobody has established that for these two cards. A `variantKey` pair
with a guessed or merely nominal description would satisfy the schema, satisfy the validator, and
be worthless at the table: the user would be asked to pick between two options they cannot
distinguish by looking.

So the blocker is data, not mechanism, and the honest interim position is the one the definition
file takes — those print runs are absent from `sets` entirely, so their cards cannot be traded
rather than being tradeable-but-ambiguous. Excluding a card is recoverable; shipping an identity
that silently conflates two different cards is not. Definition version 6 (which added HMW, HMWP and
CST) deliberately left this position unchanged: the printed-total-02 runs are still absent.

Two things follow for anyone extending this spec:

- A game's *canonical source* being authoritative does not make it complete or correct. This
  collision sits behind source data that is both — the same card appears with two different printed
  totals, and one of the affected runs is not listed at all. §4.7's tiebreaker rule settles
  disputes; it does not manufacture facts the source lacks.
- An application whose own card identity has no equivalent of `variantKey` inherits this problem
  with no way to express it. That is a real constraint on any implementation whose internal
  identity is a flat set-code-plus-number key, and it has to be solved there, not here.

### 4.7 Extending or correcting a definition file

Definition files are meant to be community-maintained (e.g. via pull requests against a public
repository), reviewed and merged by a maintainer, and validated in CI against the meta-schema plus
the cross-referential checks in §4.6. [`validate.py`](validate.py) implements those checks and is
the script referred to throughout this section; run it on every pull request that touches
`definitions/`. It deliberately does not reimplement JSON Schema validation (use a real validator
alongside it). Rule 6 compares a file against its own previous revision, so it needs the CI job
that knows the base revision: given that revision (`validate.py --base OLD.json NEW.json`) the
script checks that `finishes` is append-only and that `definitionVersion` increased, but not that
no `printedTotal` or `finishNumbering` changed, which needs a reviewer to match print runs across
the two revisions.

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

All UUIDs are **derived, not assigned by hand**, the same way as the service UUID in §3.1:

```
characteristicUUID(name) = UUIDv5(PROTOCOL_NAMESPACE_UUID, "characteristic:" + name)
```

so any implementer can compute them unilaterally, with no registry and no coordination. They are
the same for every game — only the *service* UUID is per-game.

| Characteristic | UUID | Direction | Contents |
|---|---|---|---|
| `Info` | `5694a89a-12f4-5b50-a3e8-f3e9c5409c1f` | both (read + write) | JSON: protocol version, `game`, `definitionVersion`, display handle, session `id` |
| `WantFilter` | `7ed5862c-bb74-5bac-b452-5b9704f07772` | peripheral → central (read) | the peripheral's 16-byte Want filter (§5), raw |
| `MyCandidates` | `4801119a-d9ba-599e-b27b-3e84414f847e` | central → peripheral (write) | the central's 16-byte Want filter, then its candidate card hashes |
| `PeerCandidates` | `ff30764d-13af-5664-a7f3-ea1c71a1192f` | peripheral → central (read) | the peripheral's candidate card hashes |
| `Confirmed` | `d5b48536-a5c6-5b4a-a47c-b71e522615b1` | peripheral → central (read) | the subset of `MyCandidates` the peripheral genuinely wants; empty until `MyCandidates` has been written |
| `PeerConfirmed` | `a6847f91-12e6-5d80-9f38-9af20b2e807e` | central → peripheral (write) | the subset of `PeerCandidates` the central genuinely wants |
| `Negotiation` | `a2ec42c3-94c1-594e-9297-00f4778f2915` | both (write + read) | optional: present only on devices that implement the negotiation feature (§6.5) |

`Info` is a JSON object, the only text payload here:

```json
{ "v": 1, "game": "swu", "definitionVersion": 6, "handle": "Pontus",
  "id": "3f2b8c1e-5a4d-4e0b-9c7a-1d2e3f4a5b6c", "features": { "negotiation": 1 } }
```

`handle` is a self-chosen display name, not an identity (§8) — treat it as untrusted text from a
stranger, truncate it for display, and never key anything on it.

`id` is a **session id**: a random version-4 UUID in its canonical lowercase text form
(`8-4-4-4-12` hex digits), generated when the app's trading session starts and never derived from
anything else — no hardware or account identifier, no stored value. It is not shown to the user and
not persisted. Its only purpose is to let a device recognize that two connections came from the
same peer: every device runs both roles at once, so two devices that notice each other at about the
same time each end up connected to the other, once as central and once as peripheral, and the
platform identifiers the two roles report for one physical peer are unrelated and cannot be
matched. A device that sees the same `id` from both roles knows it is one peer. An implementation
SHOULD send `id`. A receiver MUST tolerate its absence, and MUST treat a value that is not a
lowercase UUID as absent; without one it simply cannot recognize the peer across roles. Unknown
fields in `Info` MUST be ignored, so a later revision can add fields without breaking this one. The
`id` is unauthenticated (§9), and the same limits apply to it as to `handle`. While a negotiation
(§6.5) is open, a device keeps its `id` (§6.5.3).

`features` is optional and declares the **optional features** the sender implements: an object
whose keys are feature names and whose values are the highest version of that feature the sender
implements, a positive integer. A receiver MUST ignore feature names it does not know, and MUST
treat a missing `features`, a `features` that is not an object, a missing key, or a value that is
not a number with an integral value of at least 1, as "not supported". Optional features never
change `v`: that stays the major version carried by the namespace UUID (§3.1). Feature names
defined so far:

| Name | Feature | Versions |
|---|---|---|
| `negotiation` | Trade negotiation (§6.5); the version is its message format | 1 |

A device that implements none of them MAY omit `features`.

`Info` is the one **bidirectional** characteristic: the central reads the peripheral's, and writes
its own. An earlier draft had it read-only, which left the peripheral holding a completed exchange
it could not attribute to anybody — it knew which cards were wanted in both directions but had no
name to show beside them. That is the same defect §6.3 step 6 fixes for the card data, reappearing
for the peer's identity, and it would have left every peripheral-side user looking at an anonymous
match. A central MUST write its `Info` before writing `MyCandidates`.

### 6.2 Card hashes on the wire

The four candidate and confirmation characteristics carry **8-byte card hashes**, not card
identities: the 64-bit FNV-1a of the canonical byte encoding (§5.5) — *the same value* the filter's
bit positions are derived from — big-endian, concatenated with no separators or length prefix. A
payload of *n* cards is exactly 8*n* bytes, so the count is the length divided by eight.

An earlier draft specified JSON identity objects here, reasoning that "post-connection BLE MTU is
generous (commonly 185+ bytes after negotiation), so the size pressure that shaped the
advertisement layer does not apply". That was wrong twice over, and is recorded so nobody
reinstates it:

- 24 identities as JSON is **1602 bytes**, which does not fit a 185-byte MTU either.
- MTU was never the real ceiling. A GATT attribute value is capped at **512 bytes** by the ATT
  specification. Core Bluetooth performs long reads and writes transparently up to that limit and
  not one byte beyond, so 512 is a hard wall rather than a tuning parameter. Even the compact
  canonical encoding (549 bytes for 24 cards) clears it.

Hashes make the problem disappear rather than manage it: 24 cards is **192 bytes**, comfortably one
attribute value, with no chunking protocol, no lowered cap, and no second transport.

They work because **neither side ever needs to learn a card — only to recognize one it already
holds.** The central tests `PeerCandidates` against its own Want list; the peripheral tests
`MyCandidates` against its own; and each confirmation is resolved by its recipient against its own
Have list. A hash that resolves to nothing is a filter false positive, which is precisely what this
step exists to discard. At `nMax` on each side the collision probability is on the order of 10⁻¹⁷.

Reusing the filter's hash rather than defining a second one also means the published conformance
vectors already pin this encoding down: a `fnv1a64Hex` value in `vectors/swu-v1.json` *is* the wire
representation of that card, written big-endian.

`MyCandidates` is the one characteristic carrying two things: the central's 16-byte filter followed
by its candidate hashes, so its length is 16 + 8*n*. The filter has to travel with it — the
peripheral cannot compute `PeerCandidates` (its Have-cards hitting the *central's* filter) without
holding that filter, and nothing guarantees it ever scanned that peer's advertisement: both roles
run simultaneously, but which peers each role has seen is independent. Carrying both in one write
also makes the §6.3 gate atomic.

`WantFilter` is the mirror of that field for the other direction: it exists so a peer whose platform
cannot advertise its filter (§3.2 — in practice every iOS peripheral) is still fully discoverable. A
central that already read a filter from the advertisement MAY skip this read.

A device MUST NOT send more than `nMax` (§5.3) hashes in any single candidate or confirmation
payload — 192 bytes, or 208 for `MyCandidates`. A peer MUST reject a payload that is over-long, or
whose length is not a whole number of hashes.

### 6.3 Sequence

By the time two devices connect, each has (or can cheaply obtain) the other's filter, and each has
already computed its own candidate list. Both directions are then resolved in one connection:

1. Central connects to peripheral.
2. Central reads `Info`, writes its own `Info`, and reads `WantFilter` if it did not get the filter
   from the advertisement.
3. Central writes `MyCandidates` — its own Want filter, followed by hashes of its Have-cards
   that hit the peripheral's filter. This MUST be a write *with response*, and the central MUST
   wait for the response before step 4.
4. Peripheral checks `MyCandidates` against its own real Want list and keeps the result, per
   central, as the value of `Confirmed`. The peripheral MUST have done this before it responds to
   the step-3 write, so that a completed write means the answer exists. Central then reads
   `Confirmed`. *The central now knows what it holds that the peripheral genuinely wants.*
5. Central reads `PeerCandidates` — hashes of the peripheral's Have-cards that hit the central's
   filter, which the peripheral can now compute from the filter supplied in step 3. The peripheral
   MUST refuse this read (or return an empty payload) until step 3 has occurred; see §6.4.
6. Central checks `PeerCandidates` **locally**, against the hashes of its own real Want list
   already on device, and writes the surviving hashes back as `PeerConfirmed`, again with response.
   *The peripheral now knows what it holds that the central genuinely wants.*
7. Once the `PeerConfirmed` write has been acknowledged (or has failed), central disconnects. It
   MUST NOT disconnect straight after queuing that write: a disconnect issued alongside it can
   drop the write, leaving the peripheral without its half.

Steps 2 and 6 are what make one connection sufficient — 6 for the cards, 2 for the name to put
beside them. An earlier draft stopped at step 5 on the reasoning
that "each has already independently scanned the other's filter, so only one direction needs
confirming over the wire". That is true of the *central* — which ends the exchange knowing both
halves — but leaves the **peripheral knowing nothing confirmed at all**, unable to show its own user
the match. The peripheral would learn of it only when it re-ran the entire exchange in the central
role against the same peer, so the supposed saving was a duplicated connection, not a halved one.

**`Confirmed` is read, not notified.** An earlier draft had the peripheral push `Confirmed` as a
GATT notification. That does not survive contact with the transport, and is recorded so nobody
reinstates it:

- A notification must fit in a single ATT packet — the negotiated MTU minus 3 bytes, which can be as
  little as 20 — and unlike reads and writes there is no long-notification mechanism. `Confirmed`
  can be up to 192 bytes (§6.2). A payload that does not fit is truncated or dropped. Cut
  mid-hash, it is not a whole number of hashes and a receiver following §6.2 rejects it; cut on a
  hash boundary, it is silently shorter. Either way the failure looks like "the peer wants less of
  yours than it does", with no error to see.
- A notification goes to whichever centrals are subscribed unless the peripheral addresses it to
  one. With several exchanges in flight, an unaddressed notification hands one central another
  central's answer.
- It needs a subscription that is confirmed to be in place before step 3, and a failure to
  subscribe is silent.

None of that machinery is needed, because the answer is already there when the central asks. The
peripheral computes it inside the step-3 write and holds it before responding, so the write's own
response is the "ready" signal. A read then carries up to 192 bytes through the ordinary long-read
path, is answered only to the central that asked, and fails visibly if it fails. `Confirmed` is
therefore a plain readable characteristic, like `PeerCandidates`. It is per central: a peripheral
MUST return, to each central, only the answer to that central's own `MyCandidates`, and an empty
payload to a central that has not yet written one. A central MUST NOT read `Confirmed` before the
step-3 write has completed successfully; an empty payload is otherwise indistinguishable from
"the peripheral wants none of these".

Do not hold the connection open past this exchange. Cache the confirmed results locally and
continue scanning — this keeps the number of simultaneous BLE connections small regardless of how
many candidates are nearby (see §7 for how a busy room is handled).

**Bounding an exchange.** Implementations SHOULD NOT let one peer stall the others, and SHOULD NOT
repeat an exchange they have just finished:

- Apply a timeout to connecting and a separate one to the exchange itself, and abandon the
  exchange when either expires. A pending connection that never completes holds one of the small
  number of connection slots forever, and some platforms (Core Bluetooth among them) report
  nothing for it. Reference implementation: 10 s to connect, 20 s for the exchange counted from
  when the link comes up.
- After an exchange, do not reconnect to the same peer for a while. With duplicate advertisements
  reported, a stationary pair would otherwise reconnect on every scan callback (the §3.2 filter
  cache does not prevent this: it only dismisses peers with nothing for you). Reference
  implementation: 60 s after an exchange that reached a result, 10 s after one abandoned earlier.
- Treat a write that fails at any step as the end of the exchange. If the central already holds
  both halves (steps 4 and 5 done) it MAY still show that match to its own user, since only the
  peripheral's view of step 6 is at risk.

- **Wait a random moment before connecting to a newly noticed peer**, then check again that
  connecting is still worthwhile. Two devices that notice each other in the same instant would
  otherwise both connect at once, which is the collision described under `id` in §6.1, and a
  short random delay makes it likely that one of them finishes first. Reference implementation:
  a uniform random delay of 0.1 s to 1.5 s, with at most one pending delay per peer, however many
  times its advertisement is seen meanwhile.
- **Recognize a peer that has already been handled.** Keep the session `id` of every peer with
  whom an exchange has *completed* — in either role — for as long as the reconnect cooldown above.
  After reading a peer's `Info` (step 2), a central that finds its `id` in that list SHOULD
  disconnect without continuing. It MUST NOT skip because an exchange with that `id` is merely in
  progress: if each of two devices saw the other's connection in progress and both stood down,
  neither would ever exchange. Matches reported by the two roles for the same `id` SHOULD be shown
  as one; the later result replaces the earlier.

These numbers are tuning, not interoperability requirements; nothing on the wire depends on them.

### 6.4 Reciprocity: why `PeerCandidates` is gated

`PeerCandidates` is the one place where any part of a Have list leaves its device (§2), so its
exposure has to be bounded deliberately.

Without the step-3 precondition, any device could connect to a peripheral and read that subset
having disclosed nothing itself (and, since step 3 now also carries the central's filter, there
would additionally be nothing for the peripheral to compute the subset *from*). Combined with a filter crafted so that every card hits it, a
single passive attacker could walk a room and harvest every participant's complete inventory —
while remaining, by construction, a peer with nothing to offer. §5.4's bit-count ceiling makes the
crafted filter harder to build but does not prevent it (an attacker can simply insert real cards
from the game's catalog, which is small and fully enumerable — §5.1).

That the slice travels as hashes rather than identities (§6.2) changes none of this. The hash is
over a card from a small, fully enumerable catalog, so anyone can build the reverse table once and
read the slice straight off — it is a size optimization, exactly like the Bloom filter, and must
never be described as obscuring anything.

`Confirmed` needs no such gate. It is a subset of the hashes the central itself supplied in
`MyCandidates`, so reading it discloses nothing the reader did not already hold; it is empty before
the write only because there is nothing to compute it from (§6.3 step 4).

The gate therefore makes exposure *symmetric and simultaneous*: a peer learns a bounded slice of
your Have list only after committing an equally bounded slice of its own, and both slices are
capped at `nMax`. An attacker still learns up to `nMax` of your cards per exchange, and must give
up `nMax` plausible cards of its own to do it. This is a cost imposed on harvesting, not a
prevention of it.

**Do not describe this as making Have lists private.** As with the Bloom filter in §5.1, the
honest claim is bounded, reciprocal disclosure of a small slice — not confidentiality. Any product
copy promising more than that is wrong.

### 6.5 Trade negotiation (optional feature `negotiation`, format 1)

After §6.3 two people know *that* they can trade. This optional feature lets them agree *what* to
trade, phone to phone: one side sends a **suggestion** (cards to give and to get, with quantities),
the other accepts it, declines it or answers with a changed one, and either may call it off. It is
structured data, not chat: no free text travels (§1, §8). An implementation without this feature
is a complete implementation of v1; nothing in §§3–6.4 depends on it, and nothing in it changes
§§3–6.4 on the wire.

Everything in §6.5 is normative except values explicitly given as **reference values**, which are
tuning in the same sense as §6.3's timeouts. [`reference.py`](reference.py) implements the codecs,
the hash resolution and the state machine (§6.5.11), and
[`vectors/negotiation-v1.json`](vectors/negotiation-v1.json) pins them down.

#### 6.5.1 The `Negotiation` characteristic

| Characteristic | UUID | Properties |
|---|---|---|
| `Negotiation` | `a2ec42c3-94c1-594e-9297-00f4778f2915` | read + write (with response) |

Derived exactly like the others (§6.1):
`UUIDv5(PROTOCOL_NAMESPACE_UUID, "characteristic:Negotiation")`. It lives in the same per-game
service as the §6.1 characteristics. An implementation that supports the feature MUST include it in
the service while trading; one that does not MUST NOT.

- **Write** = one negotiation message (§6.5.4), at most 512 bytes. A central MUST write with
  response; a peripheral answers a write without response with the ATT error `writeNotPermitted`.
- **Read** = the **receipt** (§6.5.7) for the last message *this central* wrote *on this
  connection*, or an empty value before any write. A peripheral keeps one receipt per connection;
  a later write on the same connection replaces it.
- The peripheral validates the message, applies it and stores the receipt **before** it responds to
  the write, so a completed write means the receipt exists. This is the `MyCandidates` →
  `Confirmed` pattern of §6.3 step 4, for the same reasons; in particular there are no
  notifications (§6.3, "`Confirmed` is read, not notified").
- ATT errors are used for transport faults only: `invalidAttributeValueLength` for an empty value,
  a value over 512 bytes, or a format-1 value shorter than its 52-byte header (§6.5.6). Every other
  outcome, including every rejection, travels in the receipt. No custom ATT error codes are used.

A message never needs more than one attribute value: the largest is 494 bytes (§6.5.4), so there is
no app-level chunking or framing. A value longer than MTU − 3 is an ordinary ATT long (prepared)
write, which the platform stacks perform transparently up to 512 bytes; a receipt (21 bytes) fits
one read even at the minimum MTU.

#### 6.5.2 Feature discovery and format

A device declares the feature in its `Info` (§6.1) as `"features": { "negotiation": N }`, where `N`
is the highest negotiation **format** it implements. This document defines format 1; every
implementation of the feature MUST accept format 1.

- A sender learns support from the peer's `Info` before it offers the feature to its user. The
  definitive check is the characteristic's presence at delivery time (§6.5.3), which also covers a
  peer whose `Info` was never read.
- A sender uses `format = min(own highest, peer's N)`.
- A receiver that gets a message in a format it does not implement answers `UNSUPPORTED_FORMAT`
  with `detail` = the highest format it implements.
- New fields or message types are added only by a new format. There are no extension bytes, which
  keeps the exact-length checks of §6.5.4 strict.

#### 6.5.3 Delivery

**The sender of a message is always the BLE central, over a fresh short connection per message.**
No connection is held open for a negotiation: human think time is minutes, and holding links would
contradict §6.3 ("do not hold the connection open") and use up the small number of connection
slots. Both devices keep advertising and serving GATT while trading (§2), so either can reach the
other at any time.

Sequence for one message:

1. Find the peer's peripheral (below) and connect.
2. Discover the service and its characteristics on every connection (a peer's GATT table can
   change between connections; do not rely on a cache). If `Negotiation` is absent, the result is
   **unsupported**: disconnect and tell the user (§6.5.9).
3. Read `Info`. If its `id` is not the peer's session id, disconnect and try the next candidate.
4. Write own `Info` (as §6.3 step 2). This binds the connection to the sender's session id; the
   receiver checks the message's `from` against it (§6.5.6).
5. Write the message to `Negotiation`, with response.
6. Read `Negotiation`: the receipt. Disconnect after the read completes, not before (§6.3 step 7).

**Finding the peer by session id.** The receiver is identified only by its session id (§6.1), so the
sender finds its peripheral by reading `Info.id`: first at a platform handle it already associates
with that id (from an earlier exchange or delivery), then by scanning for the service UUID and
reading `Info` from advertisers not already known to belong to another session id. This scan MUST
NOT apply the filters of the discovery path (an RSSI floor, the §3.2 filter test, reconnect
cooldowns): the peer is already matched. Not found → **unreachable**; tell the user (§6.5.9).
Reference values: try a known handle for at most 5 s (platform addresses rotate), then scan for at
most 8 s with at most 6 probe connections; 10 s to connect and 10 s for steps 2–6; at most 25 s
for a whole delivery attempt including the search; one automatic retry after 2 s, then the user
decides. A retry sends the identical bytes (§6.5.8, Rule 3).

A device delivers one message at a time per negotiation: the next message waits until the previous
one's receipt has been read or its delivery has failed ("one outbox"). Only a `CANCEL` may replace
a message whose delivery is still in progress. Deliveries share the central's connection slots and
SHOULD take priority over new discovery connections; a device SHOULD NOT open a second connection
to a device it is already connected to for an exchange, but wait for that exchange to finish.

**Session id stability.** A receiver checks a message's `to` against its own session id, so a new
session id ends every negotiation addressed to the old one. A device MUST NOT generate a new session
id (§6.1) while it has a negotiation in state `SENT`, `RECEIVED`, `DECLINED_BY_ME`,
`DECLINED_BY_PEER` or `ACCEPTING` (§6.5.8). Otherwise §6.1 is unchanged: when no negotiation is
open, a new trading session still gets a new id.

#### 6.5.4 Messages

Binary, big-endian. Every message starts with a 52-byte header:

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | `format`: `0x01` |
| 1 | 1 | `type`: `0x01` SUGGEST, `0x02` ACCEPT, `0x03` DECLINE, `0x04` CANCEL; any other value is `MALFORMED` |
| 2 | 16 | `negotiationId`: chosen at random (a version-4 UUID, RFC 4122 byte order) by the sender of revision 1. Receivers treat it as 16 opaque bytes. |
| 18 | 16 | `from`: the sender's session id (its `Info.id`, §6.1), as the UUID's 16 bytes |
| 34 | 16 | `to`: the receiver's session id, as learned from its `Info` |
| 50 | 2 | `revision`: unsigned, 1..65535; 0 is `MALFORMED` |
| 52 | … | body, by type |

Bodies:

- **SUGGEST**: `giveCount` (1 byte), `getCount` (1 byte), then `giveCount + getCount` **lines** of
  11 bytes, give lines first. Total length exactly `54 + 11 × (giveCount + getCount)`, with
  `1 ≤ giveCount + getCount ≤ 40`; at 40 lines, 494 bytes. Give and get are **from the sender's
  point of view**: the sender's give is what the receiver gets. Either side may be empty, not both.
- **ACCEPT**: `digest`, 8 bytes: the 64-bit FNV-1a (§5.2) of the accepted SUGGEST's body (offset 52
  to the end) exactly as its author sent it. `revision` is the revision being accepted. 60 bytes.
- **DECLINE**: `reason`, 1 byte: `0` = unspecified. Values 1–255 are reserved for later formats;
  receivers MUST read an unknown value as 0. `revision` is the revision being declined. 53 bytes.
- **CANCEL**: no body; `revision` is the latest revision the sender knows. 52 bytes.

Any other length for the type is `MALFORMED`.

A **line** (11 bytes):

| Offset | Size | Field |
|---|---|---|
| 0 | 8 | `hash`: the card's 8-byte hash, exactly the §6.2 wire form |
| 8 | 1 | `finish`: the zero-based index of the card's `finish` value in the definition file's `finishes` array (for `swu`: 0 = standard, 1 = foil). For a game whose `identifier.fields` has no `finish` field, 0. |
| 9 | 1 | `quantity`: number of copies, 1..255. 0 is `MALFORMED`. An implementation's own screens MAY offer less, but it MUST accept the full range on receipt. |
| 10 | 1 | `condition`: optional card condition grade, below. |

`condition` is a single optional grade, per line, on a 7-grade scale:

| Value | Grade |
|---|---|
| 0 | not stated |
| 1 | Mint |
| 2 | Near Mint |
| 3 | Excellent |
| 4 | Good |
| 5 | Light Played |
| 6 | Played |
| 7 | Poor |

Values 8–255 are reserved; a receiver MUST read an unknown value as 0 (it does not reject the
line). A sender that does not track condition sends 0 on every line. *Informative:* an
implementation using another grading scale maps it onto this one, for example a five-grade scale
of Near Mint / Lightly Played / Moderately Played / Heavily Played / Damaged as 2 / 5 / 6 / 7 / 7.

**Line identity is `(hash, condition)`.** The same card in two stated conditions is two lines.
Within each side, lines MUST be strictly ascending by `hash` (as an unsigned 64-bit number), then
by `condition`; so no `(hash, condition)` appears twice in one side. A sender that tracks more per
line than the wire carries merges lines with the same `(hash, condition)` by adding their
quantities before sending. Out-of-order or repeated lines are `MALFORMED`. The same card MAY appear
on both sides.

Nothing else is on the wire: no prices, no free text, no vendor fields. The sender's display name
is not repeated in the message: the `Info` it writes on the same connection (§6.5.3 step 4)
carries its `handle`, under the same rules as in §6.1.

#### 6.5.5 Resolving cards

A suggestion may contain any card, not just cards on the receiver's own lists, so a receiver
resolves each line's `hash` against **every card identity it can display**: it builds a reverse
table from the hash of each canonical identity in its catalog (canonicalized per §4.5, encoded per
§5.5). Collisions are not expected (64-bit hashes, a catalog of thousands); an implementation
SHOULD assert at build time that there are none.

- If any `hash` in a SUGGEST is not in the table, the whole SUGGEST is refused with
  `UNKNOWN_CARDS`, `detail` = the number of distinct unresolvable hashes. Nothing is shown to the
  receiver's user: accepting a trade one cannot fully see is never right. The sender is told.
- Once every hash resolves, a line whose `finish` byte disagrees with the resolved identity's finish
  is `MALFORMED`. The hash alone decides the card; `finish` is a cross-check. (A foil is never
  derived by flipping a field, §4.4.1.)
- A receiver presents a suggestion **mirrored**: the sender's give lines are what the receiver
  gets, and vice versa.

The `finish` byte puts the order of a definition file's `finishes` array on the wire, which is why
that array is append-only (§4.2, §4.6 rule 6).

#### 6.5.6 Receiving a message

A peripheral judges a written value in this order and stops at the first failure:

1. Empty, or longer than 512 bytes: ATT error `invalidAttributeValueLength`, no receipt.
2. `format` (byte 0) greater than the highest format implemented: `UNSUPPORTED_FORMAT`; format 0:
   `MALFORMED`. These are judged before the length, since another format's layout is unknown.
3. Shorter than the 52-byte header: ATT error `invalidAttributeValueLength`, no receipt.
4. Structure (§6.5.4): `type`, `revision` ≠ 0, line count, exact length, quantities, line order,
   and each `finish` byte within the definition's `finishes`: else `MALFORMED`.
5. `to` is not the receiver's session id: `NOT_FOR_ME` (the wrong device, or a peer that started a
   new trading session).
6. `from` is not the session id of a peer with which the receiver completed a §6.3 exchange in its
   current trading session (a current match), or is not the `id` of the `Info` the central wrote on
   this connection (or none was written): `UNKNOWN_PEER`.
7. Rate limits (§6.5.8): `RATE_LIMITED`.
8. For a SUGGEST, card resolution (§6.5.5): `UNKNOWN_CARDS`, then `MALFORMED`.
9. The state machine (§6.5.8).

Nothing refused in steps 1–8 is shown to the user or changes any state.

A receipt echoes the message's `negotiationId` and `revision` from its header. For a refusal in
step 2 (where the header's layout is not known) both are zero.

#### 6.5.7 Receipts

21 bytes, read from `Negotiation` (§6.5.1):

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | `format`: `0x01` |
| 1 | 1 | `status`, below |
| 2 | 16 | `negotiationId`, echoed (§6.5.6) |
| 18 | 2 | `revision`, echoed |
| 20 | 1 | `detail`: see the status; otherwise 0 |

| Status | Name | Meaning |
|---|---|---|
| `0x00` | `OK` | Applied. |
| `0x01` | `DUPLICATE` | This exact message was already applied (a retry after a lost receipt). The sender treats it as `OK`. |
| `0x02` | `SUPERSEDED` | The receiver holds a newer or winning suggestion; this one was dropped. |
| `0x03` | `STALE` | ACCEPT or DECLINE of a revision that is not the receiver's current pending suggestion. |
| `0x04` | `CLOSED` | The negotiation is unknown to the receiver or has ended (accepted, cancelled or expired). |
| `0x05` | `NOT_FOR_ME` | `to` is not the receiver's session id. |
| `0x06` | `UNKNOWN_PEER` | `from` is not a current match, or not the `Info.id` written on this connection. |
| `0x07` | `UNKNOWN_CARDS` | `detail` = number of distinct hashes the receiver cannot resolve. |
| `0x08` | `BUSY` | The receiver limits concurrent negotiations and is at its limit. |
| `0x09` | `RATE_LIMITED` | Too many messages from this peer. |
| `0x0A` | `MALFORMED` | Fails §6.5.4 or the finish cross-check of §6.5.5. |
| `0x0B` | `UNSUPPORTED_FORMAT` | `detail` = the highest format the receiver implements. |

Statuses `0x0C`–`0xFF` are reserved. Only `OK` and `DUPLICATE` mean "delivered"; a sender MUST
treat any other status, an unknown status, a value that is not 21 bytes or a receipt in an unknown
format as a failed delivery. An empty read means the write did not happen.

#### 6.5.8 State machine

A device keeps, per negotiation and in memory only: its `negotiationId`, the peer's session id, the
highest `revision` seen, the **current** suggestion (its revision, its author — this device or the
peer —, its body bytes and digest), the state, the time of the last message in either direction,
the last message applied from the peer (for Rule 3), and the one message being delivered.

**States.** `SENT` (my suggestion is current and unanswered), `RECEIVED` (theirs is current and
unanswered), `DECLINED_BY_ME`, `DECLINED_BY_PEER`, `ACCEPTING` (I sent ACCEPT and have no receipt
yet), and the terminal `ACCEPTED`, `CANCELLED`, `EXPIRED`. Before any negotiation, a device is
`IDLE`. The first four are **open**; open states and `ACCEPTING` are **live**. A user editing a
reply is a UI mode on top of these, not a state; whether a delivery is in progress or failed is an
overlay, not a state.

**Rule 1: commit, then deliver.** A local action changes local state first and then delivers its
message. Incoming messages are judged against that state, one at a time and serialized with local
actions. This makes the author of the current suggestion the sole arbiter of its acceptance: an
ACCEPT is applied only if the suggestion it names is still current when it arrives.

**Rule 2: tie-break.** When two SUGGESTs compete at the same revision (both changed it at once, or
both opened a negotiation with each other at once under different ids), the one whose author's
session id is numerically smaller, as 16 unsigned bytes, wins; across negotiation ids, a higher
revision wins first. Both devices compute the same winner.

**Rule 3: duplicates.** A message byte-identical to the last message applied from the same peer in
the same negotiation is answered `DUPLICATE` and changes nothing. This is judged before everything
else in this section, so a retry after a lost receipt is recognized even when the message it
repeats ended the negotiation.

Local actions:

| Action | Allowed in | Next state | Sends |
|---|---|---|---|
| L1 Send a suggestion | `IDLE` or terminal (a new negotiation: new random id, revision 1), or an open state (revision + 1, same id) | `SENT` | SUGGEST |
| L2 Accept | `RECEIVED` | `ACCEPTING` | ACCEPT of the current revision, with its digest |
| L3 Decline | `RECEIVED` | `DECLINED_BY_ME` | DECLINE of the current revision |
| L4 End the negotiation | live | `CANCELLED` | CANCEL, best effort |
| L5 The trading session is stopped by the user, or the app terminates | live | `CANCELLED` | CANCEL, best effort (not when the app is merely paused or backgrounded) |
| L6 No message in either direction for the idle timeout, or the peer's match is removed | live | `EXPIRED` | CANCEL, best effort |

L1–L3 wait for the previous message's delivery (§6.5.3, one outbox). While a negotiation is live,
L1 for a different peer is refused locally; a device MAY support several negotiations at once, and
then keeps this state per negotiation.

Receipt for one's own ACCEPT, while `ACCEPTING`: `OK` or `DUPLICATE` → `ACCEPTED`; `STALE` → back
to `RECEIVED` (a newer suggestion is current, or will arrive); `CLOSED` → `CANCELLED`; anything else
→ stay `ACCEPTING`, delivery failed, the user may retry. Receipts for other messages change no
state; they are shown to the user (§6.5.9).

Incoming messages that passed §6.5.6 steps 1–8, in this order:

| Message | Condition | Next state | Receipt |
|---|---|---|---|
| any | Rule 3 | unchanged | `DUPLICATE` |
| any | its `negotiationId` names a negotiation this device has ended | unchanged | `CLOSED` |
| SUGGEST | no live negotiation | `RECEIVED` (a new negotiation under its id) | `OK` |
| SUGGEST | live negotiation with another peer, at the receiver's limit | unchanged | `BUSY` |
| SUGGEST | same id, `revision` > highest seen | `RECEIVED` | `OK` (replaces my suggestion, theirs, a decline, or my ACCEPT in flight) |
| SUGGEST | same id, same revision, current is theirs with the same digest | unchanged | `DUPLICATE` |
| SUGGEST | same id, same revision, state `SENT`, and theirs wins Rule 2 | `RECEIVED` | `OK` |
| SUGGEST | same id, otherwise | unchanged | `SUPERSEDED` |
| SUGGEST | another id from the peer of my live negotiation, and theirs wins Rule 2 on (revision, author; if both are the peer's, theirs) | `RECEIVED`, under their id | `OK` |
| SUGGEST | another id from that peer, otherwise | unchanged | `SUPERSEDED` |
| ACCEPT, DECLINE, CANCEL | not my live negotiation with this peer | unchanged | `CLOSED` |
| CANCEL | live | `CANCELLED` | `OK` |
| ACCEPT | state `SENT`, current is mine, same revision, same digest | `ACCEPTED` | `OK` |
| DECLINE | state `SENT`, current is mine, same revision | `DECLINED_BY_PEER` | `OK` |
| ACCEPT, DECLINE | otherwise | unchanged | `STALE` |

Who may send what follows from the table: a SUGGEST by either side in any open state (including
changing one's own unanswered suggestion), ACCEPT and DECLINE only by the side that did not author
the current suggestion and only for its revision, CANCEL by either side. `ACCEPTED` is terminal for
the wire: from then on each user only confirms, or does not, the change to their own records
(§6.5.9). A CANCEL arriving after that gets `CLOSED` and changes nothing.

How the rules resolve the races:

- **Both send a SUGGEST at once.** Rule 2. The loser's sender gets `SUPERSEDED`; it has received,
  or will receive on retry, the winning suggestion.
- **An ACCEPT crosses a change.** A replaces its revision 5 with revision 6 while B accepts 5. If A
  committed 6 first, B's ACCEPT is `STALE` and B is shown 6. If A applied the ACCEPT first, A is
  `ACCEPTED` and can no longer change revision 5.
- **A receipt is lost.** The sender retries the identical bytes and gets `DUPLICATE` (Rule 3). A
  delivery is never assumed.
- **Messages out of order** cannot happen between two devices (one outbox each); a message left
  over from an earlier connection is caught by its revision.
- **An ACCEPT is never confirmed.** If an ACCEPT's receipt never arrives, the acceptor stays
  `ACCEPTING` until the idle timeout and then `EXPIRED`; the author may already be `ACCEPTED`. The
  acceptor's user must be told that it is not known whether the peer recorded the accept; each user
  still confirms their own change, and they are standing together.

**Limits.** A device MAY limit how many negotiations it holds at once (`BUSY` beyond it). It SHOULD
limit messages per peer (`RATE_LIMITED`), counting the messages that passed §6.5.6 steps 1–6;
one answered `RATE_LIMITED` does not count.
Reference values: idle timeout 10 minutes; at most 10 messages per peer per minute and at most one
per second; one negotiation at a time.

#### 6.5.9 What every implementation must show its user

- The peer's display name is its `Info.handle`, under §6.1's rules for untrusted text.
- **Nothing in the user's collection or records changes without that user's explicit confirmation
  after an accept**, on that user's own device. An accept is agreement between the two people, not
  an instruction to either device.
- The user is told, rather than left waiting, when the peer cannot receive suggestions
  (unsupported), cannot be reached, is busy, does not know some of the cards, declined, ended the
  negotiation, or when the negotiation expired; and that nothing changed in their collection.
- Suggestions are not private (§6.5.10); product text must not call them so.

#### 6.5.10 Security

- **Nothing is authenticated.** Session ids are readable by anyone nearby (§6.1), so a device in
  range can send a suggestion or a CANCEL under a matched peer's id. The checks of §6.5.6 (a current
  match, `from` equal to the `Info.id` written on the same connection, `to` equal to the receiver's
  id) stop misdirected and stale messages, not a deliberate spoofer. The harm is bounded by
  §6.5.9's confirmation requirement: a spoofed message can annoy, or end a negotiation, but never
  change a collection. See §9.
- **Size.** A message is at most 512 bytes and is checked for its exact length; at most 40 lines;
  quantity at least 1; one stored receipt per connection. Rate limits as in §6.5.8.
- **No new personal data.** The name shown is the existing `handle`. A suggestion discloses exactly
  the cards in it, which its sender chose to send. Card hashes are not confidential (the catalog is
  enumerable, §6.4).

#### 6.5.11 Conformance

[`reference.py`](reference.py) implements this section: `encode_message` / `decode_message`,
`encode_receipt` / `decode_receipt`, `normalize_lines` (ordering and merging), `suggest_digest`,
`mirror`, `wins_tie`, `negotiated_format` (feature discovery), `build_reverse_table` /
`resolve_lines` (§6.5.5) and the state machine as a pure function, `step(config, state, event)`.
[`vectors/negotiation-v1.json`](vectors/negotiation-v1.json), generated from it with `swu` as the
worked example, holds byte vectors for every message type and receipt status, rejected inputs,
feature-discovery cases, and scenarios: sequences of events for two or three devices with the
output and state each must produce, written as plain data so they can be replayed in any language.

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
- No chat. The optional negotiation feature (§6.5) carries structured trade suggestions and their
  answers only; there is no free text in it.
- No accounts, no real identity — only a self-chosen display handle, and a random per-session id
  used solely to recognize a peer across the two roles (§6.1).
- No platform-specific transport (MultipeerConnectivity, Nearby Connections) in v1 — BLE only.
- No internet-based/marketplace trading mode.

## 9. Open items

Tracked here rather than silently assumed:
- **The definition file for SWU is incomplete and partly unverified.** `definitions/swu.json` now
  enumerates print runs derived from a real catalog, but several set codes in it are an
  application's internal naming rather than confirmed printed codes, and are flagged as such in the
  file's own `notes`. Each needs checking against `canonicalSource` before it can be treated as
  authoritative. Until then, cards in those sets may fail rule 4 of §4.5 on a peer using a
  differently-named definition file. Version 6 added CST (Costco Exclusives) under the same flag.
- **Some catalog cards have no recorded finish, and hash as `standard`.** The app treats an unknown
  finish (`null` in its finish map) as standard. Version 6 kept that rule on purpose, including for
  promos that may in fact be foil (for example P26/122 and P26/216–236), because changing it would
  change the hash of identities already tradeable under version 5 (§4.2's hard rule). Correcting
  them needs a decision on how to migrate, not just a data fix.
- **Whether a scanner can read a second 128-bit service UUID from an iOS peripheral, on both iOS
  and Android, is unverified on hardware** (§3.2). The protocol is designed so the answer does not
  affect correctness — `WantFilter` covers the negative case — but it determines whether iOS peers
  are discoverable without a connection, which is a significant difference in scan cost and battery
  in a busy room. Measure this before optimizing anything else in the transport.
- The governance process for reviewing and merging definition-file contributions (who has merge
  rights, how disputes beyond what `canonicalSource` settles are resolved) is not yet defined.
- `knownCollisions`'s shape (§4.6) is a first draft. A real collision is now known (§4.6.1) but
  cannot be registered until someone establishes how the two cards are physically distinguishable;
  expect the shape to need revision once it is exercised against a real case rather than an
  imagined one.
- `nMax` (§5.3) is derived from the filter's false-positive curve alone. Whether 24 cards is enough
  for the *product* — whether users routinely want to advertise more and find the cap frustrating —
  is a question for real use, and the answer may argue for a different discovery shape rather than
  a bigger filter, since the filter cannot grow past the §3.2 byte budget.
- Nothing in this spec authenticates a peer or binds a display handle to anything. A peer can claim
  any handle, and can present any session `id` — including one it has learned from another peer's
  `Info`, which makes a device stand down from that peer for the length of a cooldown. That is a
  nuisance rather than a disclosure, and is accepted for the same reason as the handle. A peer can
  also advertise a Want list it has no intention of honoring. This is acceptable for
  a protocol whose output is "go talk to that person", but it must not be extended toward anything
  resembling an unattended or committed trade without revisiting it.
- The negotiation feature (§6.5) inherits this. Because session ids are readable by anyone nearby,
  a device can send a trade suggestion, an answer or a cancellation under the session id of a peer
  the receiver has matched with. It cannot change anyone's collection: §6.5.9 requires the user's
  own confirmation after every accept, and that requirement is what keeps negotiation inside "go
  talk to that person". The accepted risk is a nuisance (an unwanted suggestion, a negotiation ended
  by someone else), as for `handle` and `id` above. Authenticating peers would need key exchange,
  which nothing in v1 has.
