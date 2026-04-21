# Infrastructure (Phase 8)

Production deployment configuration for Finance Trading App.

## Quick Start

```bash
# 1. Copy environment template and edit with your values
cp ../.env.template ../.env
# Edit ../.env: change POSTGRES_PASSWORD, JWT_SECRET_KEY, MINIO_SECRET_KEY

# 2. Generate TLS certificate for HTTPS
./gen-tls-cert.sh

# 3. Start all services (Docker required)
docker-compose up -d

# 4. Verify everything is running
docker-compose ps          # Check health status (should all be "healthy")
docker-compose logs -f     # View logs

# 5. Test the API
curl -k https://localhost/api/health
```

## Files

### Docker Compose
- `docker-compose.yml` — Orchestrates all services with health checks and dependencies

### Dockerfiles
- `docker/api.Dockerfile` — Multi-stage build for API server (FastAPI + Uvicorn)
- `docker/workers.Dockerfile` — Multi-stage build for background job workers (ARQ)
- `docker/trading.Dockerfile` — (Placeholder for trading engine)
- `docker/web.Dockerfile` — (Placeholder for Next.js frontend)

### Configuration
- `nginx/nginx.conf` — Reverse proxy with TLS termination, rate limiting, security headers
- `gen-tls-cert.sh` — Helper to generate self-signed TLS certificate

## Services

| Service | Port | Purpose | Health Check |
|---------|------|---------|---|
| Postgres | 5432 | Application database | `pg_isready` |
| Redis | 6379 | Cache & pub/sub | `redis-cli ping` |
| MinIO | 9000 | S3-compatible storage | HTTP `/minio/health/live` |
| API | 8000 | FastAPI application | HTTP `GET /health` |
| Workers | — | Background job processing | None (fires and forgets) |
| Nginx | 80, 443 | TLS termination & routing | HTTP `/health` |

## Usage

```bash
# View service status and health
docker-compose ps

# View logs (all services)
docker-compose logs -f

# View logs (specific service)
docker-compose logs -f api

# Stop all services
docker-compose down

# Remove all data and start fresh
docker-compose down -v
docker-compose up -d

# Scale to multiple API replicas
docker-compose up -d --scale api=3

# Scale to multiple worker replicas
docker-compose up -d --scale workers=2

# Restart a single service
docker-compose restart api

# Execute a command inside a container
docker-compose exec postgres psql -U trading_user -d trading_app
```

## Environment Variables

All configuration comes from `.env` file. See `../.env.template` for full list.

**Critical values (change for production):**
- `POSTGRES_PASSWORD` — Database password
- `JWT_SECRET_KEY` — JWT signing key (32+ random chars)
- `MINIO_SECRET_KEY` — MinIO credential

## TLS Certificates

### Development (self-signed)
```bash
./gen-tls-cert.sh
# Generates: certs/cert.pem, certs/key.pem (valid 365 days)
```

### Production (Let's Encrypt)
```bash
# Install certbot and get certificate
sudo certbot certonly --standalone -d yourdomain.com

# Copy to infra/certs/
sudo cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem certs/cert.pem
sudo cp /etc/letsencrypt/live/yourdomain.com/privkey.pem certs/key.pem
sudo chown 1000:1000 certs/*
sudo chmod 644 certs/cert.pem
sudo chmod 600 certs/key.pem
```

## Monitoring

### Health Status
```bash
docker-compose ps
# Shows: Up (healthy), Up (unhealthy), or Up (health: starting)
```

### Logs
```bash
# Follow all logs
docker-compose logs -f

# Nginx access/error
docker-compose logs -f nginx

# API application logs
docker-compose logs -f api

# Show last 50 lines
docker-compose logs --tail=50 api
```

### Resource Usage
```bash
docker stats
```

## Troubleshooting

**"Port 80 or 443 already in use"**
```bash
lsof -i :80    # Check what's using port 80
lsof -i :443   # Check what's using port 443
# Change Nginx port in docker-compose.yml ports: section
```

**"Postgres connection refused"**
```bash
docker-compose logs postgres
docker-compose restart postgres
```

**"API service keeps restarting"**
```bash
docker-compose logs api  # Check error messages
# Verify .env file exists and has required variables
```

**"HTTPS certificate verification failed"**
```bash
# For development, use -k to skip verification
curl -k https://localhost/api/health

# For production, ensure certificate is valid and mounted
ls -la certs/
```

See `../docs/deployment.md` for detailed deployment guide.

## Next Steps

1. **Configure secrets** → Edit `.env` with production values
2. **Generate TLS cert** → `./gen-tls-cert.sh` (or use Let's Encrypt)
3. **Deploy** → `docker-compose up -d`
4. **Monitor** → `docker-compose ps` and `docker-compose logs -f`
5. **Scale** → `docker-compose up -d --scale api=3 --scale workers=2`
