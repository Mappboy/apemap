# Superseded preparation checkpoint

These four files preserve the exact first preparation of the 30-case follow-up,
before any results, reviews, evidence retention or application. Integration found
that a `dict` check excluded frozen `EvidenceRecord.claim_value` mappings from
the task context. The command now checks `Mapping`, with a regression test.

The active batch was prepared afresh after preserving these bytes. Its task and
manifest hashes therefore differ, while the same 30 members, original parent,
database, decisions and retained evidence remain unchanged. No source research
or human approvals were discarded. Use the ordinary active Research batches
workspace `issue-102-p48-round-2-20261019`, not this preparation checkpoint.
