# Configuration Guide

## Configuration Overview

The API uses environment variables for all configuration. Configuration is loaded from:

1. Environment variables (highest priority)
2. `.env` file in the application root
3. Default values (lowest priority)

### Configuration Files

- `.env.example` - Template with all options documented
- `.env.development` - Development defaults (safe for local use)
- `.env.production` - Production template (requires configuration)
- `.env` - Your local configuration (not committed to git)

### Loading Configuration

```python
# Configuration is loaded automatically on startup
from app.config import settings

# Access configuration values
database_url = settings.database_url
jwt_secret = settings.jwt_secret_key
```

## Environment Variables

### Core Settings

#### ENVIRONMENT

**Description:** Deployment environment identifier

**Type:** String

**Values:** `development`, `staging`, `production`

**Default:** `development`

**Required:** Yes

**Example:**

```bash
ENVIRONMENT=production
```

**Behavior by Environment:**

- `development`: Debug logging, relaxed security, auto-reload
- `staging`: Production-like settings with verbose logging
- `production`: Strict security validation, minimal logging

---

### API Settings

#### API_TITLE

**Description:** API title shown in documentation

**Type:** String

**Default:** `APGI Standalone API`

**Required:** No

**Example:**

```bash
API_TITLE="APGI Consciousness Modeling API"
```

---

#### API_VERSION

**Description:** API version string

**Type:** String

**Default:** `1.1.0` (`app.__version__`)

**Required:** No

**Example:**

```bash
API_VERSION=1.2.0
```

---

#### HOST

**Description:** Host address to bind the API server

**Type:** String

**Default:** `0.0.0.0` (all interfaces)

**Required:** No

**Example:**

```bash
HOST=0.0.0.0  # Listen on all interfaces
HOST=127.0.0.1  # Listen on localhost only
```

---

#### PORT

**Description:** Port number for the API server

**Type:** Integer

**Default:** `8000`

**Required:** No

**Example:**

```bash
PORT=8000
```

---

### Database Configuration

#### DATABASE_URL

**Description:** PostgreSQL connection string

**Type:** String (URL format)

**Default:** None (must be set)

**Required:** Yes

**Format:**

```text
postgresql://[user]:[password]@[host]:[port]/[database]?[options]
```

**Examples:**

```bash
# Basic connection
DATABASE_URL=postgresql://apgi_user:password@localhost:5432/apgi_api

# With SSL
DATABASE_URL=postgresql://apgi_user:password@postgres.example.com:5432/apgi_api?sslmode=require

# With connection pooling
DATABASE_URL=postgresql://apgi_user:password@postgres:5432/apgi_api?pool_size=10&max_overflow=20

# With connection timeout
DATABASE_URL=postgresql://apgi_user:password@postgres:5432/apgi_api?connect_timeout=10
```

**Connection Pool Options:**

- `pool_size` - Number of connections to maintain (default: 10)
- `max_overflow` - Maximum overflow connections (default: 20)
- `pool_timeout` - Seconds to wait for connection (default: 30)
- `pool_recycle` - Recycle connections after N seconds (default: 3600)

**SSL Options:**

- `sslmode=disable` - No SSL (not recommended for production)
- `sslmode=require` - Require SSL connection
- `sslmode=verify-ca` - Verify server certificate
- `sslmode=verify-full` - Verify server certificate and hostname

**Production Requirements:**

- Must use SSL/TLS (`sslmode=require` or higher)
- Should use connection pooling
- Should set connection timeout

---

### Redis Configuration

#### REDIS_URL

**Description:** Redis connection string for caching and rate limiting

**Type:** String (URL format)

**Default:** `redis://localhost:6379/0`

**Required:** Yes

**Format:**

```text
redis://[password]@[host]:[port]/[database]
```

**Examples:**

```bash
# Basic connection
REDIS_URL=redis://localhost:6379/0

# With password
REDIS_URL=redis://:mypassword@redis.example.com:6379/0

# With SSL
REDIS_URL=rediss://:mypassword@redis.example.com:6380/0

# With connection timeout
REDIS_URL=redis://localhost:6379/0?socket_timeout=5&socket_connect_timeout=5
```

**Database Numbers:**

- `0` - Session state cache
- `1` - Celery broker (if using Redis)
- `2` - Celery result backend (if using Redis)
- `15` - Test database (for testing)

**Production Requirements:**

- Should use password authentication
- Should use SSL/TLS for remote connections
- Should enable persistence (AOF or RDB)

---

### Celery Configuration

#### CELERY_BROKER_URL

**Description:** Message broker URL for Celery task queue

**Type:** String (URL format)

**Default:** `redis://localhost:6379/1`

**Required:** Yes (if using async tasks)

**Examples:**

```bash
# Redis broker
CELERY_BROKER_URL=redis://localhost:6379/1

# RabbitMQ broker
CELERY_BROKER_URL=amqp://user:password@rabbitmq:5672//

# Redis with password
CELERY_BROKER_URL=redis://:password@redis:6379/1
```

---

#### CELERY_RESULT_BACKEND

**Description:** Result backend URL for storing task results

**Type:** String (URL format)

**Default:** `redis://localhost:6379/2`

**Required:** Yes (if using async tasks)

**Examples:**

```bash
# Redis backend
CELERY_RESULT_BACKEND=redis://localhost:6379/2

# Database backend
CELERY_RESULT_BACKEND=db+postgresql://user:pass@postgres:5432/apgi_api

# Redis with password
CELERY_RESULT_BACKEND=redis://:password@redis:6379/2
```

---

### Authentication Configuration

#### JWT_SECRET_KEY

**Description:** Secret key for signing JWT tokens

**Type:** String

**Default:** `dev-secret-key-change-in-production` (development only)

**Required:** Yes

**Production Requirements:**

- Minimum 32 characters
- Cryptographically random
- Never committed to version control
- Rotated periodically

**Generate Secure Key:**

```bash
# Python
python -c "import secrets; print(secrets.token_urlsafe(32))"

# OpenSSL
openssl rand -base64 32

# /dev/urandom
head -c 32 /dev/urandom | base64
```

**Example:**

```bash
JWT_SECRET_KEY=xK8vN2mP9qR4sT6uW7yZ0aB1cD3eF5gH8iJ9kL2mN4oP6qR8sT0uV2wX4yZ6aB8c
```

**Security Notes:**

- Different keys for different environments
- Store in secrets manager (AWS Secrets Manager, HashiCorp Vault)
- Rotate every 90 days
- Changing the key invalidates all existing tokens

---

#### JWT_ALGORITHM

**Description:** Algorithm for JWT token signing

**Type:** String

**Default:** `HS256`

**Required:** No

**Supported Values:**

- `HS256` - HMAC with SHA-256 (recommended)
- `HS384` - HMAC with SHA-384
- `HS512` - HMAC with SHA-512
- `RS256` - RSA with SHA-256 (requires public/private key pair)

**Example:**

```bash
JWT_ALGORITHM=HS256
```

---

#### JWT_ACCESS_TOKEN_EXPIRE_MINUTES

**Description:** Access token expiration time in minutes

**Type:** Integer

**Default:** `30`

**Required:** No

**Recommended Values:**

- Development: 60-120 minutes
- Production: 15-30 minutes

**Example:**

```bash
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
```

---

#### JWT_REFRESH_TOKEN_EXPIRE_DAYS

**Description:** Refresh token expiration time in days

**Type:** Integer

**Default:** `7`

**Required:** No

**Recommended Values:**

- Development: 7-30 days
- Production: 7-14 days

**Example:**

```bash
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7
```

---

### CORS Configuration

#### CORS_ORIGINS

**Description:** Comma-separated list of allowed origins for CORS

**Type:** String (comma-separated URLs)

**Default:** `*` (development only)

**Required:** Yes (production)

**Examples:**

```bash
# Single origin
CORS_ORIGINS=https://app.example.com

# Multiple origins
CORS_ORIGINS=https://app.example.com,https://admin.example.com,https://mobile.example.com

# Development (allow all)
CORS_ORIGINS=*
```

**Production Requirements:**

- Must be explicitly configured (no wildcards)
- Must use HTTPS URLs
- Should include all frontend domains

**Security Notes:**

- Wildcard (`*`) not allowed with credentials in production
- Subdomains must be listed explicitly
- Protocol (http/https) must match exactly

---

#### CORS_ALLOW_CREDENTIALS

**Description:** Allow credentials (cookies, authorization headers) in CORS requests

**Type:** Boolean

**Default:** `true`

**Required:** No

**Examples:**

```bash
CORS_ALLOW_CREDENTIALS=true
CORS_ALLOW_CREDENTIALS=false
```

**Security Notes:**

- Cannot use wildcard origins when `true`
- Required for cookie-based authentication
- Required for Authorization header

---

### Rate Limiting Configuration

#### RATE_LIMIT_ENABLED

**Description:** Enable rate limiting middleware

**Type:** Boolean

**Default:** `true`

**Required:** No

**Examples:**

```bash
RATE_LIMIT_ENABLED=true
RATE_LIMIT_ENABLED=false
```

**Recommendation:**

- `true` for production
- `false` for development (optional)

---

#### RATE_LIMIT_PER_MINUTE

**Description:** Maximum requests per minute per user/IP

**Type:** Integer

**Default:** `60`

**Required:** No

**Examples:**

```bash
RATE_LIMIT_PER_MINUTE=60   # 1 request per second
RATE_LIMIT_PER_MINUTE=300  # 5 requests per second
RATE_LIMIT_PER_MINUTE=1200 # 20 requests per second
```

**Recommendations:**

- Development: 300-600
- Production: 60-120
- High-traffic: 300-600 with multiple API instances

---

### Logging Configuration

#### LOG_LEVEL

**Description:** Minimum log level for output

**Type:** String

**Default:** `INFO`

**Required:** No

**Values:**

- `DEBUG` - Detailed debugging information
- `INFO` - General informational messages
- `WARNING` - Warning messages
- `ERROR` - Error messages
- `CRITICAL` - Critical errors only

**Examples:**

```bash
LOG_LEVEL=INFO
LOG_LEVEL=DEBUG
LOG_LEVEL=WARNING
```

**Recommendations:**

- Development: `DEBUG`
- Staging: `INFO`
- Production: `INFO` or `WARNING`

---

### Request Size Limiting

#### MAX_REQUEST_SIZE

**Description:** Maximum request body size in bytes

**Type:** Integer

**Default:** `10485760` (10 MB)

**Required:** No

**Examples:**

```bash
MAX_REQUEST_SIZE=10485760   # 10 MB
MAX_REQUEST_SIZE=52428800   # 50 MB
MAX_REQUEST_SIZE=104857600  # 100 MB
```

**Recommendations:**

- API endpoints: 10 MB
- File uploads: 50-100 MB
- Adjust based on use case

---

## Alerting Configuration

### Alerting Environment Variables

#### ALERT_WEBHOOK_URLS

**Description:** Comma-separated list of webhook URLs for alert notifications

**Type:** String (comma-separated URLs)

**Default:** None

**Required:** No

**Example:**

```bash
ALERT_WEBHOOK_URLS=https://hooks.slack.com/services/XXX/YYY/ZZZ,https://example.com/webhook
```

---

#### ALERT_SLACK_WEBHOOK_URLS

**Description:** Slack webhook URLs for alert notifications

**Type:** String (comma-separated URLs)

**Default:** None

**Required:** No

**Example:**

```bash
ALERT_SLACK_WEBHOOK_URLS=https://hooks.slack.com/services/XXX/YYY/ZZZ
```

---

#### ALERT_PAGERDUTY_INTEGRATION_KEYS

**Description:** PagerDuty integration keys for alert notifications

**Type:** String (comma-separated keys)

**Default:** None

**Required:** No

**Example:**

```bash
ALERT_PAGERDUTY_INTEGRATION_KEYS=abc123def456,xyz789ghi012
```

---

#### ALERT_TEAMS_WEBHOOK_URLS

**Description:** Microsoft Teams webhook URLs for alert notifications

**Type:** String (comma-separated URLs)

**Default:** None

**Required:** No

**Example:**

```bash
ALERT_TEAMS_WEBHOOK_URLS=https://outlook.office.com/webhook/XXX/YYY/ZZZ
```

---

#### ALERT_EMAIL_CONFIG

**Description:** SMTP configuration for email alerts (JSON string)

**Type:** String (JSON)

**Default:** None

**Required:** No

**Example:**

```bash
ALERT_EMAIL_CONFIG='{"smtp_server":"smtp.example.com","smtp_port":587,"username":"alerts@example.com","password":"password","from":"alerts@example.com","to":["admin@example.com"]}'
```

---

#### ALERT_ERROR_RATE_THRESHOLD

**Description:** Error rate threshold for triggering alerts (errors per minute)

**Type:** Integer

**Default:** `10`

**Required:** No

**Example:**

```bash
ALERT_ERROR_RATE_THRESHOLD=10
```

---

#### ALERT_ERROR_RATE_WINDOW_MINUTES

**Description:** Time window for error rate calculation (minutes)

**Type:** Integer

**Default:** `1`

**Required:** No

**Example:**

```bash
ALERT_ERROR_RATE_WINDOW_MINUTES=1
```

---

## Monitoring Configuration

### Metrics Environment Variables

#### METRICS_ENABLED

**Description:** Enable Prometheus metrics collection

**Type:** Boolean

**Default:** `true`

**Required:** No

**Example:**

```bash
METRICS_ENABLED=true
```

---

#### METRICS_PATH

**Description:** Path for metrics endpoint

**Type:** String

**Default:** `/metrics`

**Required:** No

**Example:**

```bash
METRICS_PATH=/metrics
```

---

## Distributed Tracing Configuration

### Tracing Environment Variables

#### TRACING_ENABLED

**Description:** Enable OpenTelemetry distributed tracing

**Type:** Boolean

**Default:** `false`

**Required:** No

**Example:**

```bash
TRACING_ENABLED=true
```

---

#### TRACING_SERVICE_NAME

**Description:** Service name for tracing

**Type:** String

**Default:** `apgi-api`

**Required:** No

**Example:**

```bash
TRACING_SERVICE_NAME=apgi-api
```

---

#### TRACING_JAEGER_ENDPOINT

**Description:** Jaeger collector endpoint

**Type:** String (URL)

**Default:** None

**Required:** No

**Example:**

```bash
TRACING_JAEGER_ENDPOINT=http://jaeger:14268/api/traces
```

---

#### TRACING_OTLP_ENDPOINT

**Description:** OTLP gRPC endpoint

**Type:** String (URL)

**Default:** None

**Required:** No

**Example:**

```bash
TRACING_OTLP_ENDPOINT=grpc://jaeger:4317
```

---

#### TRACING_SAMPLING_RATE

**Description:** Trace sampling rate (0.0 to 1.0)

**Type:** Float

**Default:** `1.0`

**Required:** No

**Example:**

```bash
TRACING_SAMPLING_RATE=1.0
```

---

#### TRACING_CONSOLE_EXPORTER

**Description:** Enable console trace export (development)

**Type:** Boolean

**Default:** `false`

**Required:** No

**Example:**

```bash
TRACING_CONSOLE_EXPORTER=true
```

---

## Environment-Specific Settings

### Development Environment

**Characteristics:**

- Relaxed security validation
- Debug logging enabled
- Auto-reload on code changes
- Default secrets provided (with warnings)
- CORS allows all origins

**Example `.env.development`:**

```bash
ENVIRONMENT=development
DATABASE_URL=postgresql://apgi_dev:dev_password@localhost:5432/apgi_api_dev
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/1
CELERY_RESULT_BACKEND=redis://localhost:6379/2
JWT_SECRET_KEY=dev-secret-key-change-in-production
CORS_ORIGINS=*
CORS_ALLOW_CREDENTIALS=true
RATE_LIMIT_ENABLED=false
LOG_LEVEL=DEBUG
```

**Security Warnings:**

- Default JWT secret triggers warning
- Wildcard CORS triggers warning
- Warnings are logged but don't prevent startup

---

### Staging Environment

**Characteristics:**

- Production-like configuration
- Verbose logging for debugging
- Explicit CORS origins
- Secure secrets required
- Rate limiting enabled

**Example `.env.staging`:**

```bash
ENVIRONMENT=staging
DATABASE_URL=postgresql://apgi_user:secure_password@postgres-staging:5432/apgi_api?sslmode=require
REDIS_URL=redis://:redis_password@redis-staging:6379/0
CELERY_BROKER_URL=redis://:redis_password@redis-staging:6379/1
CELERY_RESULT_BACKEND=redis://:redis_password@redis-staging:6379/2
JWT_SECRET_KEY=staging-secret-key-from-secrets-manager
CORS_ORIGINS=https://staging.example.com
CORS_ALLOW_CREDENTIALS=true
RATE_LIMIT_ENABLED=true
RATE_LIMIT_PER_MINUTE=120
LOG_LEVEL=INFO
```

---

### Production Environment

**Characteristics:**

- Strict security validation
- Minimal logging (INFO or WARNING)
- Explicit CORS origins required
- Secure secrets required (32+ characters)
- Rate limiting enabled
- SSL/TLS required for database
- Fails fast on missing/insecure configuration

**Example `.env.production`:**

```bash
ENVIRONMENT=production
DATABASE_URL=postgresql://apgi_user:${DB_PASSWORD}@postgres-prod:5432/apgi_api?sslmode=require&pool_size=10&max_overflow=20
REDIS_URL=redis://:${REDIS_PASSWORD}@redis-prod:6379/0
CELERY_BROKER_URL=redis://:${REDIS_PASSWORD}@redis-prod:6379/1
CELERY_RESULT_BACKEND=redis://:${REDIS_PASSWORD}@redis-prod:6379/2
JWT_SECRET_KEY=${JWT_SECRET_FROM_SECRETS_MANAGER}
CORS_ORIGINS=https://app.example.com,https://admin.example.com
CORS_ALLOW_CREDENTIALS=true
RATE_LIMIT_ENABLED=true
RATE_LIMIT_PER_MINUTE=60
LOG_LEVEL=INFO
MAX_REQUEST_SIZE=10485760
```

**Production Validation Rules:**

- `JWT_SECRET_KEY` must be 32+ characters
- `CORS_ORIGINS` cannot be wildcard (`*`)
- `DATABASE_URL` should use SSL (`sslmode=require`)
- All required variables must be set
- Validation failures prevent startup

---

## Configuration Validation

### Validation Rules

The API validates configuration on startup and fails fast if requirements are not met.

#### Development Validation Rules

**Warnings (logged but don't prevent startup):**

- Default JWT secret key is used
- CORS wildcard is used
- Database SSL is not enabled

**Errors (prevent startup):**

- Required variables are missing
- Invalid URL formats
- Invalid enum values

#### Production Validation Rules

**Errors (prevent startup):**

- `JWT_SECRET_KEY` is missing or < 32 characters
- `JWT_SECRET_KEY` is the default development value
- `CORS_ORIGINS` is wildcard (`*`)
- `DATABASE_URL` is missing
- `REDIS_URL` is missing
- Required variables are missing
- Invalid URL formats

### Validation Examples

**Valid Production Configuration:**

```bash
ENVIRONMENT=production
JWT_SECRET_KEY=xK8vN2mP9qR4sT6uW7yZ0aB1cD3eF5gH8iJ9kL2mN4oP6qR8sT0uV2wX4yZ6aB8c
CORS_ORIGINS=https://app.example.com
DATABASE_URL=postgresql://user:pass@postgres:5432/db?sslmode=require
```

 Passes validation

**Invalid Production Configuration:**

```bash
ENVIRONMENT=production
JWT_SECRET_KEY=short
CORS_ORIGINS=*
DATABASE_URL=postgresql://user:pass@postgres:5432/db
```

 Fails validation:

- JWT secret too short
- CORS wildcard not allowed
- Database SSL not configured

### Validation Error Messages

When validation fails, the API logs specific errors:

```text
Configuration validation failed:
- JWT_SECRET_KEY must be at least 32 characters in production (current: 5)
- CORS_ORIGINS cannot be wildcard (*) in production
- DATABASE_URL should use SSL in production (add ?sslmode=require)
```

The API then exits with code 1.

---

## Security Best Practices

### Secrets Management

**DO:**

- Use secrets manager (AWS Secrets Manager, HashiCorp Vault, etc.)
- Generate cryptographically random secrets
- Rotate secrets periodically (every 90 days)
- Use different secrets for each environment
- Restrict access to secrets (principle of least privilege)

**DON'T:**

- Commit secrets to version control
- Share secrets via email or chat
- Use the same secret across environments
- Use weak or predictable secrets
- Log secrets in application logs

### Environment Variable Handling

**DO:**

- Use `.env` files for local development
- Add `.env` to `.gitignore`
- Provide `.env.example` as template
- Document all variables
- Validate on startup

**DON'T:**

- Commit `.env` files
- Use production secrets in development
- Hardcode secrets in code
- Expose secrets in error messages

### Database Security

**DO:**

- Use SSL/TLS for connections
- Use strong passwords (16+ characters)
- Restrict network access (firewall rules)
- Use connection pooling
- Enable query logging (for auditing)

**DON'T:**

- Use default passwords
- Allow public database access
- Use root/admin accounts for application
- Disable SSL in production

### CORS Security

**DO:**

- Explicitly list allowed origins
- Use HTTPS URLs
- Include all subdomains explicitly
- Review origins periodically

**DON'T:**

- Use wildcard (`*`) with credentials
- Allow HTTP origins in production
- Use overly permissive origins

---

## Advanced Configuration

### Connection Pooling

**Database Connection Pool:**

```bash
DATABASE_URL=postgresql://user:pass@postgres:5432/db?pool_size=10&max_overflow=20&pool_timeout=30&pool_recycle=3600
```

**Parameters:**

- `pool_size=10` - Maintain 10 connections
- `max_overflow=20` - Allow 20 additional connections
- `pool_timeout=30` - Wait 30s for connection
- `pool_recycle=3600` - Recycle connections after 1 hour

**Recommendations:**

- Small deployment: `pool_size=5`, `max_overflow=10`
- Medium deployment: `pool_size=10`, `max_overflow=20`
- Large deployment: `pool_size=20`, `max_overflow=40`

### Redis Persistence

**Enable AOF (Append-Only File):**

```bash
# In redis.conf
appendonly yes
appendfsync everysec
```

**Enable RDB (Snapshots):**

```bash
# In redis.conf
save 900 1      # Save after 900s if 1 key changed
save 300 10     # Save after 300s if 10 keys changed
save 60 10000   # Save after 60s if 10000 keys changed
```

### Celery Worker Configuration

**Environment Variables:**

```bash
# Worker concurrency (number of worker processes)
CELERY_WORKER_CONCURRENCY=4

# Task time limits
CELERY_TASK_TIME_LIMIT=3600        # 1 hour hard limit
CELERY_TASK_SOFT_TIME_LIMIT=3300   # 55 minute soft limit

# Prefetch multiplier
CELERY_WORKER_PREFETCH_MULTIPLIER=4

# Result expiration
CELERY_RESULT_EXPIRES=86400  # 24 hours
```

**Recommendations:**

- CPU-bound tasks: `concurrency = CPU cores`
- I/O-bound tasks: `concurrency = 2-4 × CPU cores`
- Memory-intensive tasks: Lower concurrency

### Load Balancer Configuration

**Health Check Settings:**

```text
Health Check Path: /health/ready
Health Check Interval: 10 seconds
Health Check Timeout: 5 seconds
Healthy Threshold: 2 consecutive successes
Unhealthy Threshold: 3 consecutive failures
```

**Session Affinity:**

- Not required (API is stateless)
- Use round-robin or least-connections

### Advanced Logging Configuration

**Structured JSON Logging:**

```python
# Logs are automatically formatted as JSON
{
  "timestamp": "2024-01-15T10:30:00Z",
  "level": "INFO",
  "message": "Request completed",
  "request_id": "req_abc123",
  "method": "POST",
  "path": "/v1/sessions",
  "status_code": 201,
  "duration_ms": 45.2
}
```

**Log Aggregation:**

- Forward logs to ELK, Splunk, or cloud logging
- Use request_id for distributed tracing
- Set retention policies (30-90 days)

---

## Configuration Checklist

### Development Setup

- Copy `.env.development` to `.env`
- Update `DATABASE_URL` if needed
- Update `REDIS_URL` if needed
- Verify API starts successfully
- Check health endpoint returns 200

### Staging Setup

- [ ] Create `.env` from `.env.production` template
- [ ] Generate secure `JWT_SECRET_KEY`
- [ ] Configure `CORS_ORIGINS` with staging domain
- [ ] Configure database with SSL
- [ ] Configure Redis with password
- [ ] Verify all health checks pass
- [ ] Test authentication flow

### Production Setup

- [ ] Store secrets in secrets manager
- [ ] Generate secure `JWT_SECRET_KEY` (32+ characters)
- [ ] Configure `CORS_ORIGINS` with production domains
- [ ] Configure database with SSL and connection pooling
- [ ] Configure Redis with password and persistence
- [ ] Set `ENVIRONMENT=production`
- [ ] Set `LOG_LEVEL=INFO` or `WARNING`
- [ ] Enable rate limiting
- [ ] Verify configuration validation passes
- [ ] Test all health checks
- [ ] Test authentication flow
- [ ] Monitor logs for warnings

---

## Troubleshooting Configuration

### API Won't Start

#### Error: "JWT_SECRET_KEY must be at least 32 characters"

- Generate a secure key: `python -c "import secrets; print(secrets.token_urlsafe(32))"`
- Set in environment: `JWT_SECRET_KEY=<generated-key>`

#### Error: "CORS_ORIGINS cannot be wildcard in production"

- Set explicit origins: `CORS_ORIGINS=https://app.example.com`

#### Error: "DATABASE_URL is required"

- Set database URL: `DATABASE_URL=postgresql://user:pass@host:5432/db`

### Database Connection Errors

#### Error: "could not connect to server"

- Verify PostgreSQL is running
- Check host and port in `DATABASE_URL`
- Verify network connectivity

#### Error: "password authentication failed"

- Verify username and password in `DATABASE_URL`
- Check PostgreSQL user exists

#### Error: "SSL connection required"

- Add `?sslmode=require` to `DATABASE_URL`

### Redis Connection Errors

#### Error: "Connection refused"

- Verify Redis is running
- Check host and port in `REDIS_URL`
- Verify network connectivity

#### Error: "NOAUTH Authentication required"

- Add password to `REDIS_URL`: `redis://:password@host:6379/0`

### CORS Errors

#### Error: "CORS policy: No 'Access-Control-Allow-Origin' header"

- Add frontend domain to `CORS_ORIGINS`
- Verify protocol (http/https) matches exactly
- Check for trailing slashes

---

## Support

For configuration issues:

- Check [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- Review startup logs for validation errors
- Verify all required variables are set
- Contact DevOps team: <devops@example.com>
