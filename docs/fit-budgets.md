# Fit budgets (M3)

Resource requirements for the whole selection. Every row is a
**planning estimate** unless a source is found; no source was found
in M3. All sizes are whole bytes.

| Requirement | Bytes | Meaning |
|---|---|---|
| OS baseline (RAM) | 1610612736 (1.5 GiB) | Planning estimate: RAM the OS itself needs before any service |
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
