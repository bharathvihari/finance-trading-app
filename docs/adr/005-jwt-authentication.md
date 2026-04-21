# ADR-005: JWT Authentication (vs External Auth Provider)

## Status
Accepted

## Context
The app requires multi-user support with hard data isolation. Authentication options:

1. **External provider** (Auth0, Supabase Auth, Clerk) — offloads auth entirely.
2. **JWT self-hosted** — python-jose for token signing, passlib/bcrypt for password hashing.
3. **Session-based** — server-side sessions in Redis or Postgres.

External providers add a third-party dependency, a monthly cost at scale, and a network call
on every request (or a local JWT verification with their public key). For a personal/small-team
app that is self-hosted, this is unnecessary complexity.

Sessions require server-side state storage and don't work cleanly across multiple API server
instances without a shared session store.

## Decision
Self-hosted **JWT** using:
- `python-jose[cryptography]` — HS256 token signing with a secret key from environment.
- `passlib[bcrypt]` — password hashing; bcrypt with cost factor 12 (passlib default).
- `HTTPBearer` FastAPI security scheme — extracts the `Authorization: Bearer <token>` header.
- Token lifetime: 30 days (configurable via `jwt_expire_minutes`). Long-lived for a personal app
  to avoid constant re-login; can be shortened for a multi-user deployment.
- `get_current_user` FastAPI dependency — decodes the token, returns `CurrentUser(id, email)`,
  raises HTTP 401 on invalid/expired token. Injected into every protected route.

Token payload: `{"sub": user_id, "email": email, "exp": <unix timestamp>}`.

No refresh tokens implemented yet — a future concern if token rotation is needed.

## Consequences

### Good
- No external service dependency — works offline, no per-MAU cost.
- Stateless verification — any API server instance can validate a token without a DB round-trip.
- `get_current_user` is a single `Depends()` — trivial to add to any route.
- bcrypt is intentionally slow, protecting against offline dictionary attacks on leaked hashes.

### Bad
- Token revocation requires a blocklist (Redis) — not yet implemented. A logged-out token
  remains valid until expiry.
- Secret key rotation invalidates all active tokens — requires a coordinated rollout.
- Password reset flow (email link, token) is not yet implemented.
- `jwt_secret_key` must be set via environment variable before deployment; the default
  `"change-me-in-production"` is insecure.
