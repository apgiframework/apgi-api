# Production Dockerfile for APGI Standalone API
# Multi-stage build for minimal image size

# Build stage
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    postgresql-client \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --user --no-cache-dir --no-warn-script-location -r requirements.txt

# Runtime stage
FROM python:3.12-slim

WORKDIR /app

# Install runtime dependencies only
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user with UID 1000 and create /data for SQLite
RUN useradd -m -u 1000 -s /bin/bash apgi && \
    chown -R apgi:apgi /app && \
    mkdir -p /data && chown apgi:apgi /data && chmod 755 /data

# Copy Python dependencies from builder stage
COPY --from=builder --chown=apgi:apgi /root/.local /home/apgi/.local

# Set PATH to include user-installed packages
ENV PATH=/home/apgi/.local/bin:$PATH

# Copy application code
COPY --chown=apgi:apgi app/ ./app/
COPY --chown=apgi:apgi alembic.ini identifiers.yaml ./

# Switch to non-root user
USER apgi

# Expose API port
EXPOSE 8000

# Health check using /health endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run uvicorn server
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
