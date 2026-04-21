# Builder stage: compile dependencies
FROM python:3.12-slim as builder

WORKDIR /build

COPY apps/api/requirements.txt apps/workers/requirements.txt ./
RUN pip install --user --no-cache-dir -r requirements.txt && \
    pip install --user --no-cache-dir uvicorn[standard]

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

WORKDIR /app/apps/api
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --retries=5 --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
