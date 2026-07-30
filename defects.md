# Defects

## 2026-07-30 — the 7:45am ET run did not happen, and nothing noticed

**Observed.** No suggestion, no market snapshot and no outbox file exists for 2026-07-30. The newest
entries are all dated 2026-07-29 and were written by hand at 07:16–07:39 UTC while the journal was
being seeded.

**Root cause.** There is no scheduler. `crontab -l` is empty and no systemd timer references the desk,
so the "7:45am ET daily run" was never installed — it was described in the design documents and then
assumed to exist. Nothing checks for a missing run either, so a silent no-op is indistinguishable from
a day with no candidates.

**Why it matters more than a missed email.** The failure mode is invisible. A day with no suggestions
and a day where the process did not execute look identical in this journal, so the record cannot
support any later claim about how often the system produced signals.

**Correction.** Two parts, and the second matters more: install the schedule, and make a missing run
detectable. A run must leave a dated artifact even when it produces zero candidates, so that absence
becomes evidence rather than ambiguity.

**Status.** Open. No email was sent on 2026-07-30, and no claim that one was should appear anywhere.

## 2026-07-30 — the holdout credit counter was decorative

**Observed.** `state.json` reported `holdout_credits_remaining: 4` while `research/RESULTS.md` records
three credits spent, leaving 2. The weekly email publishes this field, so it would have reported 4.

**Root cause.** The field was a hand-maintained integer with a default of 4 and no code path anywhere
that decremented it. Research scripts spend credits; none of them touch the journal.

**Direction of the error, which is the point.** It overstated the untouched data remaining. Of the two
ways this number can be wrong, this is the dangerous one: it invites spending a credit that is already
gone, and the frozen data cannot be un-looked-at.

**Correction.** Replaced the counter with a ledger, `journal.HOLDOUT_SPENDS`, one row per credit
actually spent, naming the study and the date. The remaining count is derived from it and now overrides
whatever `state.json` says on load, so a stale file cannot resurrect the wrong figure. A test pins it,
and the previous test — which asserted "4 of 5" and was holding the defect in place — was corrected.
The same treatment was applied to the hardcoded `variants_tested: 47`, now read from the research
record and reporting 60.

**Status.** Fixed. Verified: the ledger reports 2 of 5.
