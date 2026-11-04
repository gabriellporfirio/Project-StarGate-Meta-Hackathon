# M3 live check (1 Nov 2026, 02:10 UTC)

Command (run twice; see below):

```
.venv/bin/stargate plan --sim flagship --services files,assistant \
  --research fixtures/research/flagship.json --explain \
  --json-out /tmp/p.json
```

- Run 1: exit 0, `plan is valid` (exit 0). Explanation status `ok`,
  model `muse-spark-1.3`, but the text stopped mid-sentence after
  247 chars, well under the 2048-token cap.
- Run 2 (the single allowed retry): exit 0, `plan is valid`
  (exit 0). Explanation status `ok`, model `muse-spark-1.3`,
  1253 chars, cited_ids `[]`, missing_ids `[]`, label
  `model explanation, not checked`. It also ends mid-sentence, so
  the cutoff looks systematic (the cap is probably shared with
  reasoning tokens) rather than transient. No further runs were
  made. The saved `/tmp/p.json` is from run 2.

M3 fixup note: both live runs were cut short (run 1 ends "and an",
run 2 ends "same kind of phone as you have now,"). The code at the time
labelled them `ok`; the current code detects the cutoff and would
now label both `incomplete` ("no closing punctuation"), with the
note labelled "model explanation, cut short, not checked". Exit
stays 0 either way. The 2048-token cap is unchanged (pinned by the
M3 spec; raising it is for M4's spec).

The plan decisions were identical in both runs and unaffected by
the explanation text. No fact ID was cited, so none was missing.

## Run 2 explanation text (as returned, truncated)

Here's what StarGate decided for your Acme Flagship X, in plain English. I'm not changing anything — just explaining.

### 1. What you asked for

You asked for a new system that can still do:
* **files** - storing, syncing and accessing your files
* **assistant** - a voice / helper assistant

StarGate looked at 5 different ways to get you there.

### 2. Your starting point: already_unlocked

This means the phone's bootloader lock is already off.

Think of the bootloader like the front gate that decides what system the phone is allowed to start. Normally it's locked to only allow the manufacturer's system. Yours is already open, so a new system can be installed without that extra unlocking step.

That makes installation easier, but it also means:
someone with physical access to the phone has an easier time tampering with it, and the warranty is often considered void once this is done.

### 3. The 4 options that are still possible - candidates

These are listed in ranked order, but **none has been chosen or rejected yet**. They are all still on the table:

**1. community_build**
This means replacing the whole Android system with a free, community-maintained version, like LineageOS.

In plain English: same kind of phone as you have now,
