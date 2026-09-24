# PostgreSQL claims use row locking, not a single-writer transaction

The starter serialized every write with SQLite's `BEGIN IMMEDIATE`, because SQLite has no row locks. PostgreSQL has no such statement, so the seam had to be replaced rather than ported. We kept both databases and made `immediate_transaction()` dialect-aware: SQLite still reserves the single writer, while PostgreSQL uses an ordinary transaction plus `SELECT ... FOR UPDATE SKIP LOCKED` on the rows a worker is about to claim, applied through `lock_rows_for_claim()` in `database.py`. This is what `SPEC.md` recommends and it is the reason for moving to PostgreSQL at all — a global advisory lock would have been a smaller change but would have made concurrent workers queue behind each other, giving up the only property we gained.

## Considered options

- **`pg_advisory_xact_lock` for every write.** One code path, no dialect branch, zero race risk. Rejected: it reproduces SQLite's bottleneck on a database that does not need it, and it would make the concurrency test assert that a lock exists rather than that claims are distributed.
- **Drop SQLite entirely.** Rejected: the four protocol tests run in ~2 s on SQLite and ~4 s against a PostgreSQL container. Keeping SQLite preserves the fast local loop; the dialect branch it costs is eleven lines in one module.

## Consequences

`recover_expired_in_session()` also had to take the lock. It runs inside every claim, so on PostgreSQL two workers can enter it at once; without locking the expired attempts, both would try to expire the same lease. On SQLite that was impossible and the code did not need to say so.

Two dialects mean a test can pass on one and fail on the other. The race test is therefore run against both, and it is not decorative: removing `skip_locked=True` and re-running it on PostgreSQL makes two workers claim the same task and violate `uq_attempt_task_number`. That failure was reproduced deliberately before this ADR was written.
