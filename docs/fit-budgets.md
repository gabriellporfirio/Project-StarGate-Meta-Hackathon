# Fit budgets (M3)

Resource requirements for the whole selection. Every row is a
**planning estimate** unless a source is found; no source was found
in M3. All sizes are whole bytes.

| Requirement | Bytes | Meaning |
|---|---|---|
| OS baseline (RAM) | 805306368 (768 MiB) | Planning estimate: RAM the clean system needs before any service (lowered from 1.5 GiB on 9 Nov 2026: the clean system runs only the chosen apps; not yet measured) |
| Files app overhead (RAM) | 67108864 (64 MiB) | Planning estimate: extra RAM when Files is selected |
| Assistant app overhead (RAM) | 134217728 (128 MiB) | Planning estimate: extra RAM when the Assistant is selected, before the model |
| Runtime factor | x2 | Planning estimate: RAM per model byte (weights plus working memory); multiplies the model's total file bytes |
| Files budget (storage) | 209715200 (200 MiB) | Planning estimate: storage when Files is selected |
| Assistant app size (storage) | 157286400 (150 MiB) | Planning estimate: storage for the Assistant app itself, before the model |

Memory needed = OS baseline + Files app overhead (if Files is
selected) + Assistant app overhead (if selected) + the model's total
file bytes x runtime factor (if the Assistant is selected).

Storage needed = Files budget (if Files is selected) + the Assistant
app size and the model's total file bytes (both only if the
Assistant is selected).

Because every requirement above is a planning estimate, every
memory, storage and CPU check in M3 is labelled "estimated". A
missing value (a phone fact, or a model size) never counts as "too
big": it makes the check `unknown`.

## Every service (9 Nov 2026)

The per-service numbers now live in `stargate/services.py`, one entry
per service (Files, AI assistant, Private VPN, Ad blocker, Media
library). All are planning estimates. Memory needed = OS baseline +
each selected service's overhead (+ model bytes x runtime factor).

## When no model fits: cloud only

If the Assistant is selected and no catalogue model fits, the
selection is no longer refused outright. The app-only numbers are
checked (model size 0); when they fit, the model choice is
`cloud_only`: the Assistant runs through the user's own cloud key. A
selection is refused only when even that does not fit.
