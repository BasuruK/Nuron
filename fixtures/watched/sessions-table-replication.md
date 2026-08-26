# Nightly replication of the sessions table

Ops runbook entry.

The sessions table is replicated nightly to the reporting cluster at 02:00 UTC. The job is a
straight logical dump and restore; it does not attempt to be incremental because the table is
small enough that a full pass finishes in under four minutes.

Reporting queries must never hit the primary. If you need session data for an analysis, use the
replica — the primary is on the request hot path and a slow analytical query there shows up
immediately as latency for real users.

The replica lags by up to a day by construction. Anything needing fresher data than that is a
different problem and should not be solved by tightening this schedule.
