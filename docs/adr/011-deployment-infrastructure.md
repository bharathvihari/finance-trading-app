# ADR 011: Deployment Infrastructure (Nginx, Docker, Health Checks)

**Status:** Accepted  
**Context:** Phase 8 — Production deployment setup  
**Date:** 2026-04-17

---

## Problem

The application needs production-ready deployment infrastructure that:
1. Terminates TLS at the edge (Nginx)
2. Routes requests to the API and WebSocket services
3. Enforces security headers and rate limiting
4. Monitors service health to prevent requests to broken instances
5. Runs containers securely (non-root users, minimal images)
6. Allows easy scaling and local orchestration

## Decision

Implement a Docker Compose-based deployment stack with:
1. **Nginx** as a reverse proxy with TLS termination
2. **Multi-stage Dockerfiles** to minimize production image size
3. **Non-root container users** for security hardening
4. **Health checks** on all services with Docker-managed restart
5. **Service dependency ordering** (API waits for Postgres/Redis health, etc.)
6. `.env` file for environment configuration (secrets not in code)

---

## Rationale

### Nginx (vs. custom Python reverse proxy)

| Aspect | Nginx | Python proxy |
|--------|-------|--------------|
| Startup latency | ~100ms | ~1s |
| TLS throughput | ~20k req/s | ~2k req/s |
| Maturity | 20+ years | Ad-hoc |
| Security updates | Monthly | Depends on library |
| Rate limiting | Built-in (limit_req) | Custom code |

**Choice:** Nginx. Industry standard, battle-tested, zero overhead.

### Multi-Stage Docker Builds

Reduces production image size:

```
Builder stage (Python 3.12-slim + pip):      ~300 MB
Copy runtime packages only to production:    ~100 MB
Final image (Python 3.12-slim + packages):   ~150 MB
```

Avoids shipping build tools (gcc, pip, setuptools) in production.

### Non-Root User

Running containers as root introduces privilege escalation risk:
- If container escapes, attacker gains root on host (if Docker runs as root)
- Kubernetes and secure environments reject root containers
- NIST guidelines recommend least-privilege accounts

Solution: `adduser appuser` (UID 1000) in Dockerfile, `USER appuser` before CMD.

### Health Checks (vs. restart policies alone)

| Feature | Restart policy | Health checks |
|---------|---|---|
| Restarts failed containers | ✓ | ✓ |
| Waits for readiness | ✗ | ✓ |
| Prevents cascade failures | ✗ | ✓ |
| Container state visibility | Limited | Clear (healthy/unhealthy/starting) |
| Load balancer feedback | ✗ | ✓ |

Docker waits for dependent services to report healthy before starting downstream services.

### TLS at the edge (Nginx) vs. application-level TLS

| Aspect | Nginx TLS | App TLS |
|--------|-----------|---------|
| Decryption latency | 1-2ms | Same (app must decrypt) |
| Rate limiting access | Before decryption | After decryption (wastes resources) |
| Certificate rotation | No app restart | Restart required |
| Observability | Access logs, metrics | Must instrument app |
| Multi-protocol | Easy (HTTP/1.1, HTTP/2) | Depends on framework |

**Choice:** Terminate at Nginx. Simpler operations, faster rate limiting, easier cert rotation.

---

## Deployment Workflow

1. **Local Development**
   ```bash
   cp .env.template .env
   docker-compose up -d
   curl http://localhost/api/health
   ```

2. **Production**
   ```bash
   # Generate TLS cert (or use Let's Encrypt)
   ./infra/gen-tls-cert.sh
   
   # Set strong secrets in .env
   
   # Start all services
   docker-compose up -d
   
   # Check health
   docker-compose ps  # All should show "Up (healthy)"
   ```

3. **Scaling (if needed)**
   ```bash
   docker-compose up -d --scale api=3  # Run 3 API replicas
   docker-compose up -d --scale workers=2  # Run 2 worker replicas
   # Nginx upstream auto-balances across healthy instances
   ```

---

## Security Considerations

### HTTPS Enforcement
- HTTP → HTTPS redirect at Nginx
- HSTS header (1 year) prevents downgrade attacks
- TLS 1.2+ only (no SSLv3, TLS 1.0, 1.1)
- Modern cipher suites (ECDHE for forward secrecy)

### Request Filtering
- Rate limiting: 10 req/s for API, 20 req/s for WebSocket (per IP)
- Path filtering: only `/api/`, `/ws/`, `/health` are allowed
- All other paths return 404

### Container Isolation
- Non-root user: `appuser` (UID 1000)
- No privileged mode
- Minimal attack surface (Alpine-based images where possible)

### Secrets Management
- Environment variables in `.env` (git-ignored)
- Never commit secrets to code
- Use strong random values for JWT secret, database password
- Nginx TLS cert path mounted read-only

---

## Observability

### Logs
- Nginx access/error logs: `/var/log/nginx/*.log` (available via `docker-compose logs`)
- Application logs: `docker-compose logs api`
- Worker logs: `docker-compose logs workers`

### Metrics
- Service health: `docker-compose ps` shows health status
- Container resource use: `docker stats`
- API endpoint latency: Observable via Nginx access logs

### Monitoring (Future)
- Prometheus scrape of `/metrics` (if added to API)
- Alert on service unhealthy state
- ELK stack for centralized logs (if scaling beyond single machine)

---

## Trade-offs

### ✓ What we gain
- Production-ready TLS termination
- Automated service restart and health monitoring
- Minimal image size and attack surface
- Easy horizontal scaling (multi-container)
- Clear separation of concerns (Nginx routing, app logic)

### ✗ What we trade away
- Complexity: local dev requires Docker (mitigated by clear setup docs)
- Debugging: must use `docker exec` to inspect containers
- Single-machine deployment limit: Compose doesn't scale across hosts (use Kubernetes if needed)

---

## Related ADRs
- [ADR 004: FastAPI Backend](004-fastapi-backend.md) — Application framework
- [ADR 008: ARQ Async Job Queue](008-arq-async-job-queue.md) — Background job processing
- [ADR 010: Postgres User Data Isolation](010-postgres-user-data-isolation.md) — Data security

---

## References
- Nginx: https://nginx.org/en/docs/
- Docker best practices: https://docs.docker.com/develop/dev-best-practices/
- OWASP TLS Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Transport_Layer_Protection_Cheat_Sheet.html
