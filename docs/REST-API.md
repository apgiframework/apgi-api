# APGI API

This is the REST API for the APGI consciousness modeling system. The API provides a complete interface for managing simulation sessions, executing experimental tasks, and accessing consciousness modeling results.

## Features

- **Session Management**: Create, control, and monitor simulation sessions
- **Session Templates**: Create, manage, and reuse session configuration templates
- **API Key Management**: Create, rotate, and manage API keys for programmatic access
- **Webhook Delivery Management**: Monitor, retry, and manage webhook deliveries including dead-letter queues
- **Asynchronous Task Execution**: Run long-running experiments via Celery task queue
- **JWT Authentication**: Secure API access with role-based permissions
- **Data Export**: Export session data in JSON and CSV formats, retrieve summary statistics, time series data, and event analysis
- **Health Monitoring**: Comprehensive health checks and Prometheus metrics
- **Business Metrics Dashboard**: Real-time operational metrics for users, sessions, tasks, and templates
- **Alerting System**: Multi-channel alerting (webhook, Slack, Teams, PagerDuty, email) for critical errors
- **Distributed Tracing**: OpenTelemetry integration for request tracing
- **Horizontal Scaling**: Stateless design supports multiple API instances
- **Production Ready**: Docker deployment, database migrations, graceful shutdown

## Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL 14+
- Redis 7+
- Docker and Docker Compose (recommended for local development)

### Local Development Setup (Docker Compose - Recommended)

The fastest way to get started is using Docker Compose, which sets up all services automatically:

1. **Clone the repository and navigate to the standalone-api directory:**

   ```bash
   cd standalone-api
   ```

2. **Start all services:**

   ```bash
   docker-compose -f deployment/docker-compose.yml up
   ```

3. **Access the API:**

- API: <http://localhost:8000>
- Interactive docs: <http://localhost:8000/docs>
- Health check: <http://localhost:8000/health>
- Metrics: <http://localhost:8000/metrics>

The Docker Compose setup includes:

- API server with hot-reload
- PostgreSQL database
- Redis cache
- Celery worker for async tasks

### Local Development Setup (Manual)

If you prefer to run services manually:

1. **Copy environment configuration:**

   ```bash
   cp .env.development .env
   ```

2. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   pip install -r requirements-dev.txt  # For development tools
   ```

3. **Start PostgreSQL and Redis:**

   ```bash
   # Using Docker Compose for just the data services
   docker-compose -f deployment/docker-compose.yml up -d postgres redis
   ```

4. **Run database migrations:**

   ```bash
   alembic upgrade head
   ```

5. **Start the API server:**

   ```bash
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

6. **Start Celery worker (in a separate terminal):**

   ```bash
   celery -A app.celery_app worker --loglevel=info
   ```

7. **Access the API:**

- API: <http://localhost:8000>
- Interactive docs: <http://localhost:8000/docs>
- Health check: <http://localhost:8000/health>

## API Endpoints

### Authentication

- `POST /v1/auth/login` - User login (returns access and refresh tokens)
- `POST /v1/auth/refresh` - Refresh access token
- `POST /v1/auth/logout` - Logout and invalidate tokens

### API Keys

- `POST /v1/api-keys` - Create new API key
- `GET /v1/api-keys` - List user's API keys
- `GET /v1/api-keys/{key_id}` - Get API key details
- `PUT /v1/api-keys/{key_id}` - Update API key metadata
- `POST /v1/api-keys/{key_id}/rotate` - Rotate API key (generate new secret)
- `DELETE /v1/api-keys/{key_id}` - Delete API key

### Sessions

- `POST /v1/sessions` - Create new simulation session
- `GET /v1/sessions/{id}` - Get session details
- `POST /v1/sessions/{id}/start` - Start or resume session
- `POST /v1/sessions/{id}/pause` - Pause session
- `POST /v1/sessions/{id}/stop` - Stop session
- `POST /v1/sessions/{id}/reset` - Reset session to initial state
- `DELETE /v1/sessions/{id}` - Delete session

### Session Templates

- `GET /v1/templates` - List session templates (public and user's own)
- `POST /v1/templates` - Create new session template
- `GET /v1/templates/{template_id}` - Get template details
- `PUT /v1/templates/{template_id}` - Update template
- `DELETE /v1/templates/{template_id}` - Delete template

### State Queries

- `GET /v1/sessions/{id}/state` - Get current simulation state
- `GET /v1/sessions/{id}/metrics` - Get simulation metrics

### Async Tasks

- `POST /v1/tasks` - Submit asynchronous task
- `GET /v1/tasks/{task_id}` - Get task status
- `GET /v1/tasks/{task_id}/result` - Get task result
- `DELETE /v1/tasks/{task_id}` - Cancel task

### Data Export

- `GET /v1/sessions/{id}/export/json` - Export session as JSON
- `GET /v1/sessions/{id}/export/csv` - Export session as CSV
- `GET /v1/sessions/{id}/export/summary` - Get summary statistics
- `GET /v1/sessions/{id}/export/timeseries` - Get time series data
- `GET /v1/sessions/{id}/export/events` - Get event analysis data

### Webhook Deliveries

- `GET /v1/webhooks/deliveries` - List webhook deliveries
- `GET /v1/webhooks/deliveries/{delivery_id}` - Get delivery details
- `POST /v1/webhooks/deliveries/{delivery_id}/retry` - Retry failed delivery
- `DELETE /v1/webhooks/deliveries/{delivery_id}` - Delete delivery
- `GET /v1/webhooks/dead-letter` - List dead-letter webhook deliveries
- `GET /v1/webhooks/dead-letter/{delivery_id}` - Get dead-letter delivery details
- `POST /v1/webhooks/dead-letter/{delivery_id}/retry` - Retry dead-letter delivery
- `DELETE /v1/webhooks/dead-letter/{delivery_id}` - Delete dead-letter delivery

### Business Metrics

- `GET /v1/metrics/dashboard` - Complete dashboard data
- `GET /v1/metrics/overview` - High-level overview metrics
- `GET /v1/metrics/sessions` - Session-related metrics
- `GET /v1/metrics/tasks` - Task-related metrics
- `GET /v1/metrics/users` - User-related metrics
- `GET /v1/metrics/templates` - Template-related metrics

### Health & Monitoring

- `GET /health` - Basic health check
- `GET /health/ready` - Readiness probe (checks dependencies)
- `GET /health/live` - Liveness probe
- `GET /metrics` - Prometheus metrics
- `GET /version` - API version information

### Research Pilot (Participants, Consent, Studies, Batteries, Trials)

- `POST /v1/participants` - Enroll a participant
- `GET /v1/participants/{id}` - Get a participant
- `DELETE /v1/participants/{id}` - Right-to-erasure (tombstones + enqueues cascading deletion)
- `POST /v1/participants/{id}/consents` - Grant a versioned consent (`research_participation` or `data_sharing`)
- `POST /v1/participants/{id}/consents/revoke` - Revoke a consent
- `GET /v1/participants/{id}/consents/status` - Effective status of both required consents
- `POST /v1/studies` / `GET /v1/studies` / `GET /v1/studies/{id}` - Studies (staff-only create)
- `POST /v1/batteries` / `GET /v1/batteries/{id}` - Behavioural test batteries (staff-only create)
- `POST /v1/participant-sessions` - Start a participant's administration of a battery
- `POST /v1/participant-sessions/{id}/trials` - Ingest a batch of trial events
- `POST /v1/participant-sessions/{id}/complete` - Session-completion contract (idempotent; triggers scoring)

### Scoring, Norms, Psychometrics & Project Metadata

- `GET /v1/norms` - Percentile + bootstrap CI against pooled empirical scores
- `GET /v1/instrument/psychometrics` - Cronbach's alpha and related reliability stats
- `GET /v1/meta` - Citation/DOI/licence metadata, generated from `identifiers.yaml`
- `GET /v1/dataset-card` - Dataset card (release state, ethics disclosures, live counts)

### Longitudinal Metrics & n-of-1 Experiments

- `GET /v1/longitudinal/metrics` - ICC, SEM, MDC95 between two repeated-measures indices
- `GET /v1/participants/{id}/change-report` - MDC-gated change classification (active subscription or staff)
- `POST /v1/n-of-1/experiments` / `GET /v1/n-of-1/experiments/{id}` - Single-subject alternating-phase experiments
- `POST /v1/n-of-1/experiments/{id}/observations` - Record an outcome observation
- `GET /v1/n-of-1/experiments/{id}/analysis` - Phase means/SDs and a two-phase permutation-test contrast
- `GET /v1/sessions/{id}/state-vector` - Full 7-dimensional latent state vector (K7b-gated, withheld by default)

For detailed API documentation, visit <http://localhost:8000/docs> when the server is running.

## Configuration

The API uses environment variables for configuration. See `.env.example` for all available options.

### Environment Files

- `.env.example` - Template with all configuration options documented
- `.env.development` - Development defaults (safe for local development)
- `.env.production` - Production template (requires configuration)

### Key Configuration Variables

|Variable|Description|Required|
|----------|-------------|----------|
|`ENVIRONMENT`|Environment: development, staging, or production|Yes|
|`DATABASE_URL`|PostgreSQL connection string|Yes|
|`REDIS_URL`|Redis connection string|Yes|
|`JWT_SECRET_KEY`|Secret key for JWT token signing (32+ chars)|Yes|
|`CORS_ORIGINS`|Comma-separated list of allowed origins|Yes (production)|

### Environment-Specific Behavior

**Development:**

- Debug logging enabled
- Auto-reload on code changes
- Relaxed security validation
- Default JWT secret provided (with warning)

**Production:**

- Strict security validation
- Requires explicit configuration
- Fails fast on missing/insecure settings
- No default secrets

## Project Structure

```text
standalone-api/
├── app/                    # Application code
│   ├── config.py          # Configuration management
│   ├── main.py            # FastAPI application entry point
│   ├── database/          # Database models and connection
│   ├── middleware/        # Middleware components
│   ├── models/            # Pydantic request/response models
│   ├── routes/            # API route handlers
│   ├── services/          # Business logic services
│   ├── tasks/             # Celery async tasks
│   └── utils/             # Utility functions
├── tests/                 # Test suite
│   ├── unit/             # Unit tests
│   ├── integration/      # Integration tests
│   └── property/         # Property-based tests
├── deployment/           # Deployment configuration
│   ├── Dockerfile        # Production Docker image
│   └── docker-compose.yml # Local development orchestration
├── docs/                 # Documentation
└── scripts/              # Utility scripts
```

## Development

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov-report=html

# Run specific test types
pytest tests/unit/          # Unit tests only
pytest tests/integration/   # Integration tests only
pytest tests/property/      # Property-based tests only

# Run specific test file
pytest tests/unit/test_config.py

# Run tests with verbose output
pytest -v
```

### Code Quality

```bash
# Format code
black app/ tests/

# Sort imports
isort app/ tests/

# Lint code
flake8 app/ tests/

# Type checking
mypy app/
```

### Database Migrations

```bash
# Create a new migration
alembic revision --autogenerate -m "Description of changes"

# Apply migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1

# View migration history
alembic history

# View current version
alembic current
```

### Utility Scripts

The `scripts/` directory contains helpful utilities:

```bash
# Start development environment
./scripts/start.sh          # Linux/Mac
./scripts/start.ps1         # Windows

# Run database migrations
./scripts/migrate.sh        # Linux/Mac
./scripts/migrate.ps1       # Windows

# Check API health
./scripts/health_check.sh   # Linux/Mac
./scripts/health_check.ps1  # Windows
```

## Deployment

See [DEPLOYMENT.md](docs/DEPLOYMENT.md) for detailed deployment instructions.

### Docker Deployment

```bash
# Build production image
docker build -t apgi-api:latest -f deployment/Dockerfile .

# Run with Docker Compose
docker-compose -f deployment/docker-compose.yml up
```

## Documentation

- [Configuration Guide](docs/CONFIGURATION.md) - Complete configuration reference
- [Deployment Guide](docs/DEPLOYMENT.md) - Production deployment instructions
- [Troubleshooting Guide](docs/TROUBLESHOOTING.md) - Common issues and solutions
- [API Documentation](<http://localhost:8000/docs>) - Interactive API docs (when server is running)

## Architecture

The standalone API follows a layered architecture:

- **Routes Layer**: FastAPI route handlers for HTTP endpoints
- **Services Layer**: Business logic and orchestration
- **Database Layer**: SQLAlchemy ORM models and connection management
- **Middleware Stack**: Authentication, CORS, rate limiting, logging, metrics
- **Task Queue**: Celery workers for asynchronous task execution
- **Cache Layer**: Redis for session state and rate limiting

Key design principles:

- Stateless API instances for horizontal scaling
- Request-scoped database sessions
- JWT-based authentication with role-based access control
- Structured JSON logging for observability
- Graceful shutdown with in-flight request completion

## Security

- **Authentication**: JWT tokens with 30-minute expiration
- **Authorization**: Role-based access control (RBAC)
- **CSRF Protection**: Token validation for state-changing operations
- **Rate Limiting**: Configurable per-user/IP limits
- **Input Validation**: Pydantic schema validation on all requests
- **Password Hashing**: Bcrypt with appropriate cost factor
- **Secrets Management**: Environment variables, never in code

### Rate Limiting

The API implements rate limiting to prevent abuse and ensure fair resource allocation. Rate limits are applied per authenticated user and IP address.

**Rate Limit Headers:**

All API responses include the following headers to inform clients of their current rate limit status:

- `X-RateLimit-Limit`: Maximum number of requests allowed per time window
- `X-RateLimit-Remaining`: Number of requests remaining in the current time window
- `X-RateLimit-Reset`: Unix timestamp when the rate limit window resets

**Default Limits:**

- Authenticated users: 60 requests per minute
- Unauthenticated requests: 30 requests per minute

**Rate Limit Exceeded:**

When rate limits are exceeded, the API returns HTTP 429 (Too Many Requests) with a JSON response:

```json
{
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "Rate limit exceeded. Try again later.",
    "retry_after": 60
  }
}
```

The `retry_after` field indicates the number of seconds to wait before retrying.

**Configuration:**

Rate limiting can be configured via environment variables:

- `RATE_LIMIT_ENABLED`: Enable/disable rate limiting (default: true)
- `RATE_LIMIT_PER_MINUTE`: Requests per minute per user/IP (default: 60)

**Note:** Rate limiting is currently not implemented (MF-01). This documentation describes the intended behavior.

## Monitoring

The API provides comprehensive monitoring capabilities for operational insights and alerting:

- **Health Checks**: `/health`, `/health/ready`, `/health/live`
- **Metrics**: Prometheus metrics at `/metrics`
- **Business Metrics**: Dashboard data at `/v1/metrics/dashboard` (authenticated)
- **Structured Logging**: JSON logs with request IDs for tracing
- **Error Tracking**: Automatic error logging with context
- **Distributed Tracing**: OpenTelemetry integration for request tracing

### Alerting System

The API includes a sophisticated alerting system that monitors critical errors and sends notifications through multiple channels:

**Supported Notification Channels:**

- **Webhook**: HTTP POST to custom endpoints
- **Slack**: Messages to Slack channels via webhooks
- **Microsoft Teams**: Adaptive cards to Teams channels
- **PagerDuty**: Incident creation for critical alerts
- **Email**: SMTP-based email notifications
- **Log**: Structured logging for audit trails

**Alert Types:**

- **Error Rate Alerts**: Triggered when error rates exceed thresholds
- **Custom Alerts**: Manually triggered alerts from application code
- **Escalation Policies**: Automatic severity escalation based on alert age

**Configuration:**
Alerting is configured via environment variables:

- `ALERT_WEBHOOK_URLS`: Comma-separated list of webhook URLs
- `ALERT_SLACK_WEBHOOK_URLS`: Slack webhook URLs
- `ALERT_PAGERDUTY_INTEGRATION_KEYS`: PagerDuty integration keys
- `ALERT_TEAMS_WEBHOOK_URLS`: Microsoft Teams webhook URLs
- `ALERT_EMAIL_CONFIG`: SMTP configuration for email alerts
- `ALERT_ERROR_RATE_THRESHOLD`: Error rate threshold (default: 10/minute)
- `ALERT_ERROR_RATE_WINDOW_MINUTES`: Error rate window (default: 1 minute)

### Business Metrics Dashboard

The API provides business intelligence metrics for operational dashboards:

**Available Metrics:**

- **User Metrics**: Total users, active users, registration trends
- **Session Metrics**: Session creation, completion rates, template usage
- **Task Metrics**: Task completion rates, performance by type
- **Template Metrics**: Most used templates, template creation trends

**API Endpoints:**

- `GET /v1/metrics/dashboard` - Complete dashboard data
- `GET /v1/metrics/overview` - High-level overview metrics
- `GET /v1/metrics/sessions` - Session-related metrics
- `GET /v1/metrics/tasks` - Task-related metrics
- `GET /v1/metrics/users` - User-related metrics
- `GET /v1/metrics/templates` - Template-related metrics

### Distributed Tracing

The API integrates OpenTelemetry for distributed tracing:

**Tracing Configuration:**

- `TRACING_SERVICE_NAME`: Service name for traces (default: "apgi-api")
- `TRACING_JAEGER_ENDPOINT`: Jaeger collector endpoint
- `TRACING_OTLP_ENDPOINT`: OTLP gRPC endpoint
- `TRACING_SAMPLING_RATE`: Trace sampling rate (default: 1.0)
- `TRACING_CONSOLE_EXPORTER`: Enable console trace export (development)

**Instrumented Components:**

- FastAPI request/response cycles
- SQLAlchemy database queries
- Redis cache operations
- HTTP client requests
- Async task execution

### Key Metrics Tracked

- Request rate and response times (p50, p95, p99)
- Error rates by endpoint and type
- Active sessions and tasks
- Database connection pool usage
- Cache hit rates and performance
- Business metrics (users, sessions, tasks, templates)
- Alert triggering and delivery success rates

## Troubleshooting

### Common Issues

**API won't start:**

- Check that PostgreSQL and Redis are running
- Verify environment variables are set correctly
- Check logs for configuration validation errors

**Database connection errors:**

- Verify `DATABASE_URL` is correct
- Ensure PostgreSQL is accessible from the API container
- Check database credentials

**Authentication failures:**

- Verify `JWT_SECRET_KEY` is set and matches across instances
- Check token expiration times
- Ensure clock synchronization across servers

For more detailed troubleshooting, see [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## Contributing

### Development Workflow

1. Create a feature branch from `main`
2. Make your changes with tests
3. Run code quality checks: `black`, `isort`, `flake8`, `mypy`
4. Run test suite: `pytest`
5. Submit pull request with description

### Testing Requirements

- Unit tests for new functions and classes
- Integration tests for API endpoints
- Property-based tests for universal properties
- Minimum 80% code coverage

## License

Copyright © 2024 APGI System
