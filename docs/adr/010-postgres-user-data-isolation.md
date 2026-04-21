# ADR-010: Hard Per-User Data Isolation via user_id FK on All Application Tables

## Status
Accepted

## Context
The app is multi-user. Dashboard requirements §6 require "hard data isolation" — each user must
only see their own portfolios, broker connections, backtests, dashboard configs, and alerts.

Implementation options:
- **Row-level security (RLS)** in Postgres — enforced at the DB engine level, invisible to the API.
- **Application-level filtering** — every query adds `WHERE user_id = :current_user_id`.
- **Separate schemas per user** — one Postgres schema per user. Impractical at scale.

## Decision
Use **application-level `user_id` FK filtering** on all tables, combined with a consistent
FastAPI dependency pattern.

Every application table (except `users` itself) carries:
```sql
user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE
```

Every API route that returns user-owned data includes:
```python
current_user: CurrentUser = Depends(get_current_user)
# ... WHERE user_id = %(user_id)s
```

The `get_current_user` dependency raises HTTP 401 before any DB query executes if the token
is invalid. No application table query is issued without a validated `user_id`.

Postgres RLS was considered but not adopted at this stage: RLS requires setting a session
variable (`SET LOCAL app.user_id = ...`) on each connection, which is awkward with a connection
pool and adds DB-level complexity before it is needed at this scale.

## Consequences

### Good
- Simple to reason about — isolation is visible in every query, not hidden in a DB policy.
- Consistent pattern: add `current_user: CurrentUser = Depends(get_current_user)` and
  add `WHERE user_id = %s` — no exceptions.
- `ON DELETE CASCADE` means deleting a user removes all their data automatically.
- Index on `user_id` on every table ensures the WHERE clause is fast.

### Bad
- Application-level filtering can be accidentally omitted from a new route — no DB-level safety net.
  (RLS would enforce it even if the API forgot.) This is mitigated by code review and tests.
- A single compromised JWT gives full access to that user's data — token revocation
  (see ADR-005) must be implemented before production multi-user deployment.
