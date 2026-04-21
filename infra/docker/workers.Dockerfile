# Builder stage: compile dependencies
FROM python:3.12-slim as builder

WORKDIR /build

COPY apps/workers/requirements.txt ./
RUN pip install --user --no-cache-dir -r requirements.txt

# Production stage: minimal runtime image
FROM python:3.12-slim as production

WORKDIR /app

# Create non-root user for security
RUN addgroup --gid 1000 appuser && \
    adduser --uid 1000 --gid 1000 --disabled-password --gecos '' appuser

# Copy only necessary files from builder
COPY --from=builder --chown=appuser:appuser /root/.local /home/appuser/.local
COPY --chown=appuser:appuser . /app

# Set PATH for user-installed packages
ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER appuser

WORKDIR /app/apps/workers

CMD ["python", "-m", "arq", "apps.workers.arq_worker.WorkerSettings"]
