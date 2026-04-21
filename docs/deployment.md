# Phase 8: Deployment & Infrastructure

This document covers production deployment setup for the Finance Trading App.

## Overview

Phase 8 implements infrastructure-as-code for containerized deployment with:
- **Nginx reverse proxy** with TLS termination and HTTP→HTTPS redirect
- **Multi-stage Docker builds** for smaller production images
- **Non-root container users** for security hardening
- **Health checks** on all services (Postgres, Redis, API, Workers, Nginx)
- **Service dependencies** ensuring startup order correctness
- **Environment configuration** via `.env.template` documentation

No new application code is added in Phase 8 — all business logic is complete through Phase 7.

---

## Files Created/Modified

### New Files
- `.env.template` — Environment variables documentation (copy to `.env` before running)
- `infra/nginx/nginx.conf` — Reverse proxy config with TLS, rate limiting, security headers

### Modified Files
- `infra/docker-compose.yml` — Added health checks, service dependencies, nginx service
- `infra/docker/api.Dockerfile` — Multi-stage build, non-root user
- `infra/docker/workers.Dockerfile` — Multi-stage build, non-root user

---

## Setup Instructions

### 1. Environment Configuration

```bash
cp .env.template .env
# Edit .env with your actual values (database password, JWT secret, etc.)
```

**Critical values to change:**
- `POSTGRES_PASSWORD` — Default is weak; use a strong random password
- `JWT_SECRET_KEY` — Use a 32+ character random string from `openssl rand -base64 32`
- `MINIO_SECRET_KEY` — S3-compatible storage credential

### 2. TLS Certificate Setup (HTTPS)

For local development (self-signed):
```bash
mkdir -p infra/certs

# Generate self-signed certificate valid for 365 days
openssl req -x509 -newkey rsa:4096 -nodes \
  -out infra/certs/cert.pem \
  -keyout infra/certs/key.pem \
  -days 365 \
  -subj "/CN=localhost"
```

For production:
- Use a certificate from Let's Encrypt (via Certbot) or your CA
- Place files at `infra/certs/cert.pem` and `infra/certs/key.pem`
- Nginx mounts these read-only

### 3. Start Services

```bash
docker-compose -f infra/docker-compose.yml up -d
```

**What happens:**
1. Postgres starts, runs initialization scripts, becomes healthy
2. Redis starts and becomes healthy
3. MinIO starts with S3 endpoint
4. API service waits for Postgres & Redis health checks, then starts
5. Workers service starts, runs ARQ job queue
6. Nginx starts, waits for API health check, then listens on ports 80/443

All services have `restart: unless-stopped` for resilience.

### 4. Verify Deployment

```bash
# Check all services are running and healthy
docker-compose -f infra/docker-compose.yml ps

# View logs
docker-compose -f infra/docker-compose.yml logs -f api
docker-compose -f infra/docker-compose.yml logs -f workers

# Test API endpoint (via Nginx reverse proxy)
curl -k https://localhost/api/health  # Returns 200 if healthy

# Test WebSocket endpoint
wscat -c "wss://localhost/ws/trades?token=<JWT_TOKEN>"
```

---

## Nginx Routing

All traffic flows through Nginx with TLS termination:

| Path | Upstream | Purpose |
|------|----------|---------|
| `/api/*` | `api:8000` | REST API endpoints, rate limited to 10 req/s |
| `/ws/*` | `api:8000` | WebSocket connections, rate limited to 20 req/s |
| `/health` | `api:8000` | Health check endpoint (no auth) |
| `/*` | — | Returns 404 (only `/api/` and `/ws/` allowed) |

**Security features:**
- TLS 1.2+ with modern cipher suites
- HSTS header (31536000 seconds / 1 year)
- X-Frame-Options, X-Content-Type-Options, X-XSS-Protection headers
- Rate limiting per IP address
- Gzip compression enabled

---

## Docker Image Improvements

### Multi-Stage Builds
Reduces production image size by ~200MB:
- Builder stage: installs all pip dependencies
- Production stage: copies only compiled packages, excludes build tools

### Non-Root User
Containers run as `appuser` (UID 1000) instead of root:
- Prevents privilege escalation attacks
- Docker daemon still runs as root; user ID applies inside container only

### Health Checks
All services report health status:
```bash
docker-compose -f infra/docker-compose.yml ps
# Shows: Up (healthy), Up (unhealthy), or Up (health: starting)
```

Prevents routing traffic to unhealthy containers.

---

## Scaling Considerations

### Horizontal Scaling (Multiple API Instances)
To run multiple API replicas:
1. Remove `container_name` from `api` service in docker-compose.yml
2. Change `ports` to single host port mapping (e.g., `8000:8000`)
3. Increase replicas: `docker-compose up -d --scale api=3`
4. Nginx upstream automatically load-balances across all healthy instances

### ARQ Worker Scaling
Multiple worker instances can process jobs in parallel:
```bash
docker-compose up -d --scale workers=3
```
Each worker connects to the same Redis queue and claims tasks independently.

---

## Monitoring & Troubleshooting

### Check Service Health
```bash
docker-compose -f infra/docker-compose.yml exec api curl http://localhost:8000/health
docker-compose -f infra/docker-compose.yml exec -T redis redis-cli ping
```

### View Logs
```bash
# All services
docker-compose -f infra/docker-compose.yml logs -f

# Specific service
docker-compose -f infra/docker-compose.yml logs -f workers --tail=50
```

### Restart a Service
```bash
docker-compose -f infra/docker-compose.yml restart api
```

### Stop Everything
```bash
docker-compose -f infra/docker-compose.yml down
```

---

## Environment Variables Reference

See `.env.template` for complete list. Key categories:

- **API Server**: `API_HOST`, `API_PORT`
- **Database**: `POSTGRES_HOST`, `POSTGRES_PASSWORD`, etc.
- **Cache/Queue**: `REDIS_HOST`, `REDIS_PORT`
- **Auth**: `JWT_SECRET_KEY`, `JWT_ALGORITHM`, `JWT_EXPIRE_MINUTES`
- **Storage**: `PARQUET_ROOT`, `DUCKDB_PATH`, `HOT_WINDOW_MONTHS`
- **Reverse Proxy**: `NGINX_TLS_CERT`, `NGINX_TLS_KEY`

All variables are read from `.env` at container start time via `env_file:` directive.

---

## Security Checklist

- [ ] Change `POSTGRES_PASSWORD` to strong random value
- [ ] Change `JWT_SECRET_KEY` to strong random value (32+ chars)
- [ ] Change `MINIO_SECRET_KEY` for production
- [ ] Replace self-signed TLS cert with production certificate
- [ ] Review Nginx security headers in `infra/nginx/nginx.conf`
- [ ] Enable Docker log rotation on host (prevents disk fill)
- [ ] Use `.env` for secrets (not checked into git)
- [ ] Keep Docker images updated (`docker-compose pull`)
- [ ] Run security scans on images (e.g., Trivy)

---

## Troubleshooting Common Issues

### "Postgres connection refused"
- Check Postgres is healthy: `docker-compose ps postgres`
- Verify credentials in `.env` match `docker-compose.yml`
- Check logs: `docker-compose logs postgres`

### "API service keeps restarting"
- Check logs: `docker-compose logs api`
- Verify `.env` file exists and has required variables
- Ensure Postgres and Redis are healthy first

### "HTTPS certificate verification failed"
- For development: use `-k` flag with curl or ignore cert warnings
- For production: ensure certificate is valid and mounted at `infra/certs/`

### "Port 80/443 already in use"
- Check what's using the port: `lsof -i :80` or `netstat -tuln`
- Change Nginx port mapping in docker-compose.yml
- Or stop the conflicting service first

---

## Next Steps

After Phase 8:
1. **Run in production**: `docker-compose -f infra/docker-compose.yml up -d`
2. **Monitor**: Set up alerting on container health status
3. **Scale**: Add more API/worker replicas as load increases
4. **Observe**: Collect logs and metrics (implement ELK or similar)
5. **Iterate**: Update `docker-compose.yml` and Dockerfiles as needed

All application code is complete. Future work is purely operational (monitoring, scaling, optimization).
