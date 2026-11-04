# Install rules (M5)

Fixed rules for checking a model-drafted install plan. Code in
`stargate/install.py` follows these rules exactly. A draft is accepted
only if every rule passes and no step is conditional.

A listed check is never its answer: `check_oem_unlocking` and
`check_is_logical` only tell the person what to look at, and code
never treats them as having returned a value.

| Rule ID | Kind | Facts read |
|---|---|---|
| I-FORMAT | policy | none (reads the provider reply) |
| I-QUOTE | policy | none (reads citations and fetched pages) |
| I-TYPES | policy | none (reads step types, partitions, modes) |
| I-STATE | policy | boot.bootloader_locked |
| I-DYNAMIC | source | boot.dynamic_partitions |
| I-UNKNOWN | policy | boot.bootloader_locked, boot.dynamic_partitions |
| I-UNLOCK | source | boot.bootloader_locked, boot.oem_unlock_supported, boot.oem_unlock_allowed |
| I-WIPE | source | boot.bootloader_locked |

Kinds: a **source** rule rests on a fact from one of the two fetched
Android pages and carries an anchor quote below, the exact sentence
from that page. A **policy** rule is our own project, schema or
provider rule; it carries a reference to its place in our docs and no
Android quote.

## I-FORMAT

Kind: policy. Facts: none (reads the provider reply).

Reference: M5 spec section 3 rule I-FORMAT, schema
`install-draft-v1`, and the provider contract (`finish_reason`
`stop`).

The reply parses against `install-draft-v1`, and the provider says it
finished normally (`finish_reason` `stop`). A truncated, malformed or
non-stop reply is a rejected draft, never repaired by code. Steps are
non-empty unless `do_not_install` is set, in which case steps and
changes are both empty. Changes are non-empty otherwise.

## I-QUOTE

Kind: policy. Facts: none (reads citations and fetched pages).

Reference: M5 spec sections 2 and 3, the citation rule.

Every quote is non-empty and appears word for word inside one
source range of the excerpt sent from the page its record names
(after whitespace is normalised). A quote from page text that was
not sent, or one spanning a "[…]" gap, is rejected. Each step cites
the rule it relies on. The steps that rest on source rules must cite
that rule with its anchor quote: `unlock_bootloader` cites I-UNLOCK,
`reboot_fastbootd` and `flash` cite I-DYNAMIC, and the erase warning
cites I-WIPE. `do_not_install` cites I-UNLOCK. Steps resting only on
policy rules cite the rule ID, with no Android quote. Any other step
or change cites a listed rule ID, with a quote exactly when the cited
rule is a source rule.

## I-TYPES

Kind: policy. Facts: none (reads step types, partitions, modes).

Reference: M5 spec section 2, the closed step types and the closed
partition list (`system` only); this is project scope.

Only the nine step types, only partitions from the closed list
(`system`), only modes `bootloader` and `fastbootd`, and only change
kinds `removed`, `added` and `changed`.

## I-STATE

Kind: policy. Facts: boot.bootloader_locked.

Reference: M5 spec section 3, the phone-state table. This table is our
own project rule. It is marked policy because no single sentence was
pinned word for word from the fetched page during the build; the
build ran with no live fetch, so nothing was borrowed from Android.

Code walks the draft step by step with an explicit phone state: the
current mode (`system`, `bootloader` or `fastbootd`), whether the
bootloader is known to be unlocked, whether OEM unlocking was
checked, and whether the erase warning was given. The walk starts
from the profile: mode `system`, unlocked only if
`boot.bootloader_locked` is `false` (measured or inferred).

| Step | Allowed only when | Then |
|---|---|---|
| `check_oem_unlocking` | mode `system` | OEM unlocking was checked |
| `reboot_bootloader` | mode `system` | mode `bootloader` |
| `unlock_bootloader` | mode `bootloader`, OEM unlocking checked, the erase warning already given, and the phone not already unlocked | unlocked |
| `reboot_fastbootd` | mode `bootloader` and unlocked | mode `fastbootd` |
| `check_is_logical` | mode `bootloader` or `fastbootd` | the next step must be `stop_for_check` |
| `stop_for_check` | directly after `check_is_logical` or `check_oem_unlocking`, as the last step | the plan ends; result `unconfirmed` |
| `flash` | unlocked, and the step's mode equals the current mode | (no change) |
| `wipe_data` | mode `bootloader` or `fastbootd`, and unlocked | (no change) |
| `reboot_system` | the last step | mode `system` |

## I-DYNAMIC

Kind: source. Facts: boot.dynamic_partitions.

Page: https://source.android.com/docs/core/architecture/bootloader/fastbootd

If `boot.dynamic_partitions` is `true`, `flash system` must use mode
`fastbootd`. If `false`, it must use mode `bootloader`. When the fact
is `unknown` or `conflicting`, I-UNKNOWN governs instead. Anchor:

> The bootloader must not allow the flashing or erasing of dynamic partitions and must return an error if these operations are attempted.

## I-UNKNOWN

Kind: policy. Facts: boot.bootloader_locked, boot.dynamic_partitions.

Reference: M5 spec section 3 rule I-UNKNOWN.

When a critical fact is `unknown` or `conflicting`, exactly one
ending is legal, the plan contains no `unlock_bootloader`, `flash` or
`wipe_data`, and the result is `unconfirmed`:

- lock state unknown (dynamic partitions known or not):
  `check_oem_unlocking`, then `stop_for_check`;
- dynamic partitions unknown with lock state known:
  `reboot_bootloader`, then `check_is_logical system`, then
  `stop_for_check`;
- both unknown: the lock-state ending, because it comes first.

When the lock state is known and the unlock is not supported,
I-UNLOCK governs instead and the only legal draft is `do_not_install`.

## I-UNLOCK

Kind: source. Facts: boot.bootloader_locked, boot.oem_unlock_supported, boot.oem_unlock_allowed.

Page: https://source.android.com/docs/setup/test/running

Already unlocked (`boot.bootloader_locked` `false`): the plan must
not contain `unlock_bootloader`, and it is valid even when
`boot.oem_unlock_supported` is `false`. Locked with unlock not
supported (`boot.oem_unlock_supported` `false`): only `do_not_install`.
Locked with unlock confirmed (supported and allowed both `true`): a
normal plan may be accepted. Locked with unlock not confirmed
(supported or allowed is `unknown`, `conflicting`, or allowed is
`false`): `check_oem_unlocking` must be present and unconditional,
every step after it must be marked conditional, code still runs the
state table on those steps as if the condition holds, but the result
is `unconfirmed`, never `accepted`, and a warning alone never changes
that. `do_not_install` is legal exactly when the phone is locked and
the unlock is not confirmed. When the lock state is unknown, I-UNKNOWN
governs instead. Anchor:

> If OEM unlocking remains unavailable, your device might be SIM locked by your carrier and the bootloader can't be unlocked.

## I-WIPE

Kind: source. Facts: boot.bootloader_locked.

Page: https://source.android.com/docs/setup/test/running

Before `unlock_bootloader`, the plan warns that unlocking erases the
phone, quoting the page's sentence. Anchor:

> For privacy reasons, unlocking the bootloader deletes user data.
