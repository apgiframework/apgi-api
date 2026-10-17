# Troubleshooting Guide

This guide provides solutions to common issues encountered when deploying
and operating the APGI Standalone API.

## Table of Contents

- [Quick Diagnostics](#quick-diagnostics)
- [Startup Issues](#startup-issues)
- [Database Issues](#database-issues)
- [Redis Issues](#redis-issues)
- [Authentication Issues](#authentication-issues)
- [API Key Issues](#api-key-issues)
- [Session Template Issues](#session-template-issues)
- [Webhook Delivery Issues](#webhook-delivery-issues)
- [Data Export Issues](#data-export-issues)
- [Business Metrics Issues](#business-metrics-issues)
- [Alerting Issues](#alerting-issues)
- [Distributed Tracing Issues](#distributed-tracing-issues)
- [Performance Issues](#performance-issues)
- [Celery Worker Issues](#celery-worker-issues)
- [CORS Issues](#cors-issues)
- [Logging and Monitoring](#logging-and-monitoring)
- [Docker Issues](#docker-issues)
- [Kubernetes Issues](#kubernetes-issues)
- [Network Issues](#network-issues)
- [Data Issues](#data-issues)
- [Getting Help](#getting-help)

## Quick Diagnostics

### Health Check Commands

```bash
# Basic health check
curl http://localhost:8000/health

# Readiness check (verifies dependencies)
curl http://localhost:8000/health/ready

# Liveness check
curl http://localhost:8000/health/live

# Metrics
curl http://localhost:8000/metrics
```

### Log Commands

```bash
# Docker Compose
docker-compose logs -f api
docker-compose logs -f celery_worker
docker-compose logs --tail=100 api

# Kubernetes
kubectl logs -f deployment/apgi-api
kubectl logs -f deployment/apgi-celery-worker
kubectl logs --tail=100 deployment/apgi-api

# Search for errors
docker-compose logs api | grep ERROR
kubectl logs deployment/apgi-api | grep ERROR
```

### Service Status Commands

```bash
# Docker Compose
docker-compose ps
docker-compose top

# Kubernetes
kubectl get pods
kubectl get deployments
kubectl describe pod <pod-name>
```

## Startup Issues

### API Won't Start

**Symptom:** API container exits immediately or fails to start

**Common Causes:**

#### - Configuration Validation Failure

**Error Message:**

```bash
Configuration validation failed:
- JWT_SECRET_KEY must be at least 32 characters in production
```

**Solution:**

```bash
# Generate a secure JWT secret
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Set in environment
export JWT_SECRET_KEY=<generated-key>

# Or update .env file
echo "JWT_SECRET_KEY=<generated-key>" >> .env
```

#### - Missing Required Environment Variables

**Error Message:**

```bash
DATABASE_URL is required
```

**Solution:**

```bash
# Check which variables are missing
docker-compose config

# Set required variables
export DATABASE_URL=postgresql://user:pass@postgres:5432/apgi_api
export REDIS_URL=redis://redis:6379/0

# Or update .env file
cat >> .env << EOF
DATABASE_URL=postgresql://user:pass@postgres:5432/apgi_api
REDIS_URL=redis://redis:6379/0
EOF
```

#### - Port Already in Use

**Error Message:**

```bash
Error: Address already in use
```

**Solution:**

```bash
# Find process using port 8000
lsof -i :8000
# Or on Windows
netstat -ano | findstr :8000

# Kill the process
kill -9 <PID>

# Or change the port
export PORT=8001
```

#### - Import Errors

**Error Message:**

```bash
ModuleNotFoundError: No module named 'fastapi'
```

**Solution:**

```bash
# Reinstall dependencies
pip install -r requirements.txt

# Or rebuild Docker image
docker-compose build --no-cache api
```

### Database Connection Failure on Startup

**Symptom:** API fails to connect to database during startup

**Error Message:**

```text
could not connect to server: Connection refused
```

**Diagnosis:**

```bash
# Check if PostgreSQL is running
docker-compose ps postgres
kubectl get pods -l app=postgres

# Check PostgreSQL logs
docker-compose logs postgres
kubectl logs -l app=postgres

# Test connection manually
psql -h localhost -U apgi_user -d apgi_api
```

**Solutions:**

- **PostgreSQL not running:**

```bash
# Start PostgreSQL
docker-compose up -d postgres
kubectl apply -f postgres-deployment.yaml
```

- **Wrong connection string:**

```bash
# Verify DATABASE_URL format
echo $DATABASE_URL
# Should be: postgresql://user:pass@host:port/database

# Update if incorrect
export DATABASE_URL=postgresql://apgi_user:password@postgres:5432/apgi_api
```

- **Network connectivity:**

```bash
# Test network connectivity
ping postgres-host
telnet postgres-host 5432

# Check Docker network
docker network ls
docker network inspect <network-name>
```

- **Authentication failure:**

```bash
# Verify credentials
psql -h postgres-host -U apgi_user -d apgi_api

# Reset password if needed
psql -h postgres-host -U postgres
ALTER USER apgi_user WITH PASSWORD 'new_password';
```

## Database Issues

### Slow Database Queries

**Symptom:** API responses are slow, database queries taking too long

**Diagnosis:**

```sql
-- Check slow queries
SELECT pid, now() - pg_stat_activity.query_start AS duration, query
FROM pg_stat_activity
WHERE state = 'active' AND now() - pg_stat_activity.query_start > interval '1 second';

-- Check table sizes
SELECT schemaname, tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename))
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;

-- Check missing indexes
SELECT schemaname, tablename, attname, n_distinct, correlation
FROM pg_stats
WHERE schemaname = 'public' AND n_distinct > 100
ORDER BY abs(correlation) ASC;
```

**Solutions:**

- **Add missing indexes:**

```sql
-- Create index on frequently queried columns
CREATE INDEX idx_sessions_user_id ON sessions(user_id);
CREATE INDEX idx_sessions_created_at ON sessions(created_at);
```

- **Increase connection pool:**

```bash
# Update DATABASE_URL
DATABASE_URL=postgresql://user:pass@host:5432/db?pool_size=20&max_overflow=40
```

- **Optimize queries:**

```python
# Use select_related for foreign keys
session = db.query(Session).options(selectinload(Session.user)).first()

# Use pagination for large result sets
sessions = db.query(Session).limit(100).offset(0).all()
```

### Database Connection Pool Exhausted

**Symptom:** API returns 500 errors, logs show "connection pool exhausted"

**Error Message:**

```text
sqlalchemy.exc.TimeoutError: QueuePool limit of size 10 overflow 20 reached
```

**Diagnosis:****

```bash
# Check active connections
psql -h postgres-host -U apgi_user apgi_api -c "SELECT count(*) FROM pg_stat_activity;"

# Check connection pool metrics
curl http://localhost:8000/metrics | grep database_connections
```

**Solutions:**

- **Increase pool size:**

```bash
DATABASE_URL=postgresql://user:pass@host:5432/db?pool_size=20&max_overflow=40
```

- **Fix connection leaks:**

```python
# Always use context managers
with get_db_context() as db:
    # Use database
    pass
# Connection automatically closed

# Or use dependency injection
@app.get("/endpoint")
def endpoint(db: Session = Depends(get_db)):
    # Use database
    pass
# Connection automatically closed
```

- **Reduce connection timeout:**

```bash
DATABASE_URL=postgresql://user:pass@host:5432/db?pool_timeout=10
```

### Database Migration Failures

**Symptom:** Alembic migrations fail to apply

**Error Message:**

```bash
alembic.util.exc.CommandError: Can't locate revision identified by 'abc123'
```

**Diagnosis:**

```bash
# Check current version
alembic current

# Check migration history
alembic history

# Check database alembic_version table
psql -h postgres-host -U apgi_user apgi_api -c "SELECT * FROM alembic_version;"
```

**Solutions:**

- **Reset migration state:**

```bash
# Stamp database with current version
alembic stamp head

# Or stamp with specific version
alembic stamp <revision_id>
```

- **Fix broken migration:**

```bash
# Rollback to previous version
alembic downgrade -1

# Fix migration file
# Edit alembic/versions/<revision>.py

# Reapply migration
alembic upgrade head
```

- **Start fresh (development only):**

```bash
# Drop all tables
psql -h postgres-host -U apgi_user apgi_api -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

# Run all migrations
alembic upgrade head
```

## Redis Issues

### Redis Connection Refused

**Symptom:** API fails to connect to Redis

**Error Message:**

```text
redis.exceptions.ConnectionError: Error 111 connecting to redis:6379.
Connection refused.
```

**Diagnosis:**

```bash
# Check if Redis is running
docker-compose ps redis
kubectl get pods -l app=redis

# Test connection
redis-cli -h redis-host ping
# Should return: PONG

# Check Redis logs
docker-compose logs redis
kubectl logs -l app=redis
```

**Solutions:**

- **Start Redis:**

```bash
docker-compose up -d redis
kubectl apply -f redis-deployment.yaml
```

- **Fix connection string:**

```bash
# Verify REDIS_URL
echo $REDIS_URL
# Should be: redis://host:6379/0

# Update if incorrect
export REDIS_URL=redis://redis:6379/0
```

- **Check network connectivity:**

```bash
# Test connectivity
telnet redis-host 6379

# Check Docker network
docker network inspect <network-name>
```

### Redis Authentication Failed

**Symptom:** Redis connection fails with authentication error

**Error Message:**

```text
redis.exceptions.AuthenticationError: NOAUTH Authentication required
```

**Solution:**

```bash
# Add password to REDIS_URL
export REDIS_URL=redis://:password@redis:6379/0

# Or configure Redis without password (development only)
# In redis.conf:
# requirepass ""
```

### Redis Memory Issues

**Symptom:** Redis running out of memory, evicting keys

**Diagnosis:**

```bash
# Check Redis memory usage
redis-cli -h redis-host INFO memory

# Check evicted keys
redis-cli -h redis-host INFO stats | grep evicted
```

**Solutions:**

- **Increase Redis memory:**

```yaml
# docker-compose.yml
redis:
  image: redis:7-alpine
  command: redis-server --maxmemory 2gb --maxmemory-policy allkeys-lru
```

- **Configure eviction policy:**

```bash
# In redis.conf
maxmemory 2gb
maxmemory-policy allkeys-lru  # Evict least recently used keys
```

- **Reduce TTL on cached data:**

```python
# Set shorter TTL for session cache
redis_client.setex(f"session:{session_id}", 3600, session_data)
# 1 hour instead of 24
```

## Authentication Issues

### JWT Token Invalid

**Symptom:** API returns 401 Unauthorized for authenticated requests

**Error Message:**

```json
{
  "error": {
    "code": "INVALID_TOKEN",
    "message": "Invalid or expired token"
  }
}
```

**Diagnosis:**

```bash
# Decode JWT token (without verification)
python -c "import jwt; print(jwt.decode('YOUR_TOKEN', options={'verify_signature': False}))"
# Check token expiration
# exp field should be in the future
```

**Solutions:**

- **Token expired:**

```bash
# Get new token via refresh endpoint
curl -X POST http://localhost:8000/v1/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token":"YOUR_REFRESH_TOKEN"}'
```

- **Wrong JWT secret:**

```bash
# Verify JWT_SECRET_KEY is same across all API instances
echo $JWT_SECRET_KEY

# Update if different
export JWT_SECRET_KEY=<correct-secret>
```

- **Clock skew:**

```bash
# Synchronize system clocks
ntpdate -s time.nist.gov

# Or use NTP service
systemctl start ntpd
```

## API Key Issues

### API Key Creation Failed

**Symptom:** Unable to create new API key

**Error Message:**

```json
{
  "error": {
    "code": "API_KEY_CREATION_FAILED",
    "message": "Failed to create API key"
  }
}
```

**Diagnosis:**

```bash
# Check user permissions
curl -H "Authorization: Bearer <token>" http://localhost:8000/v1/users/me

# Check API key limits
# Some users may have a maximum number of API keys
```

**Solutions:**

- **Check user permissions:**
  - Ensure user has `api_key:create` permission
  - Contact admin if permission is missing

- **Check API key limits:**
  - Delete unused API keys before creating new ones
  - Contact admin to increase limit

### API Key Not Working

**Symptom:** API requests with API key return 401 Unauthorized

**Error Message:**

```json
{
  "error": {
    "code": "INVALID_API_KEY",
    "message": "Invalid or expired API key"
  }
}
```

**Diagnosis:**

```bash
# Check if key is active
curl -H "Authorization: Bearer <api_key>" http://localhost:8000/v1/api-keys

# Check key expiration
# API keys don't expire, but can be deactivated
```

**Solutions:**

- **Verify key is correct:**
  - Copy the full key (not just the prefix)
  - Ensure no extra spaces or characters

- **Check key status:**
  - API key may have been deactivated
  - Contact admin to reactivate

- **Rotate key:**

  ```bash
  curl -X POST http://localhost:8000/v1/api-keys/{key_id}/rotate \
    -H "Authorization: Bearer <token>"
  ```

### API Key Rotation Failed

**Symptom:** Unable to rotate API key

**Error Message:**

```json
{
  "error": {
    "code": "KEY_ROTATION_FAILED",
    "message": "Failed to rotate API key"
  }
}
```

**Solutions:**

- **Check permissions:**
  - Ensure user has `api_key:rotate` permission
  - Contact admin if permission is missing

- **Check key status:**
  - Key must be active to rotate
  - Reactivate key if deactivated

## Session Template Issues

### Template Not Found

**Symptom:** Template ID returns 404 Not Found

**Error Message:**

```json
{
  "error": {
    "code": "TEMPLATE_NOT_FOUND",
    "message": "Template not found or unauthorized"
  }
}
```

**Diagnosis:**

```bash
# Check if template exists
curl -H "Authorization: Bearer <token>" http://localhost:8000/v1/templates/{template_id}

# Check if template is public or owned by user
curl -H "Authorization: Bearer <token>" http://localhost:8000/v1/templates
```

**Solutions:**

- **Check template ownership:**
  - Public templates are accessible to all users
  - Private templates only accessible to owner
  - Verify you own the template or it's public

- **Check template ID:**
  - Verify template ID is correct
  - List all templates to find correct ID

### Template Creation Failed

**Symptom:** Unable to create new session template

**Error Message:**

```json
{
  "error": {
    "code": "TEMPLATE_CREATION_FAILED",
    "message": "Failed to create template"
  }
}
```

**Diagnosis:**

```bash
# Check template name conflicts
# Template names must be unique per user
```

**Solutions:**

- **Use unique name:**
  - Template names must be unique per user
  - Use descriptive names to avoid conflicts

- **Check permissions:**
  - Ensure user has `template:create` permission
  - Contact admin if permission is missing

## Webhook Delivery Issues

### Webhook Delivery Failed

**Symptom:** Webhook delivery status shows "failed"

**Diagnosis:**

```bash
# Check delivery details
curl -H "Authorization: Bearer <token>" \
  http://localhost:8000/v1/webhooks/deliveries/{delivery_id}

# Check delivery attempts
# Look at http_status_code and response_body
```

**Solutions:**

- **Check webhook URL:**
  - Verify webhook URL is accessible
  - Check for SSL certificate issues
  - Test URL manually with curl

- **Retry delivery:**

  ```bash
  curl -X POST http://localhost:8000/v1/webhooks/deliveries/{delivery_id}/retry \
    -H "Authorization: Bearer <token>"
  ```

- **Check webhook payload:**
  - Verify payload is valid JSON
  - Check payload size limits

### Dead-Letter Queue Issues

**Symptom:** Webhooks accumulating in dead-letter queue

**Diagnosis:**

```bash
# Check dead-letter deliveries
curl -H "Authorization: Bearer <token>" \
  http://localhost:8000/v1/webhooks/dead-letter
```

**Solutions:**

- **Retry dead-letter deliveries:**

  ```bash
  curl -X POST http://localhost:8000/v1/webhooks/dead-letter/{delivery_id}/retry \
    -H "Authorization: Bearer <token>"
  ```

- **Fix webhook endpoint:**
  - Fix issues with webhook URL
  - Update webhook configuration

- **Delete failed deliveries:**

  ```bash
  curl -X DELETE http://localhost:8000/v1/webhooks/dead-letter/{delivery_id} \
    -H "Authorization: Bearer <token>"
  ```

## Data Export Issues

### Export Timeout

**Symptom:** Data export request times out

**Diagnosis:**

```bash
# Check session size
# Large sessions may take longer to export

# Check export format
# CSV export may be slower than JSON
```

**Solutions:**

- **Use summary export instead:**

  ```bash
  curl -H "Authorization: Bearer <token>" \
    http://localhost:8000/v1/sessions/{session_id}/export/summary
  ```

- **Export in chunks:**
  - Use pagination for large datasets
  - Export specific time ranges

- **Increase timeout:**
  - Adjust server timeout configuration

### Export Returns Empty Data

**Symptom:** Export endpoint returns empty data

**Diagnosis:**

```bash
# Check session has data
curl -H "Authorization: Bearer <token>" \
  http://localhost:8000/v1/sessions/{session_id}

# Check session state
# Session must have data to export
```

**Solutions:**

- **Start session:**
  - Session must be running or completed to have data
  - Start session before exporting

- **Check session ownership:**
  - Verify you own the session
  - Check session permissions

## Business Metrics Issues

### Metrics Not Available

**Symptom:** Business metrics endpoints return empty data

**Diagnosis:**

```bash
# Check metrics endpoint
curl -H "Authorization: Bearer <token>" \
  http://localhost:8000/v1/metrics/dashboard

# Check user permissions
# Some metrics require admin permissions
```

**Solutions:**

- **Check permissions:**
  - Ensure user has `metrics:view` permission
  - Contact admin if permission is missing

- **Check data availability:**
  - Metrics require data to calculate
  - Ensure system has been running for some time

## Alerting Issues

### Alerts Not Triggering

**Symptom:** Expected alerts not being sent

**Diagnosis:**

```bash
# Check alert configuration
echo $ALERT_ERROR_RATE_THRESHOLD
echo $ALERT_WEBHOOK_URLS

# Check error rate
curl http://localhost:8000/metrics | grep error
```

**Solutions:**

- **Check alert configuration:**
  - Verify alert thresholds are set correctly
  - Verify webhook URLs are correct

- **Check error rate:**
  - Error rate must exceed threshold to trigger
  - Check monitoring window configuration

- **Test alert manually:**
  - Trigger test alert to verify configuration

### Alert Delivery Failed

**Symptom:** Alert webhook returns error

**Diagnosis:**

```bash
# Check webhook response
# Look at alert logs for delivery status
```

**Solutions:**

- **Fix webhook URL:**
  - Verify webhook URL is accessible
  - Check for SSL certificate issues

- **Check webhook payload:**
  - Verify payload is valid JSON
  - Check payload size limits

## Distributed Tracing Issues

### Traces Not Appearing

**Symptom:** Traces not visible in Jaeger

**Diagnosis:**

```bash
# Check tracing is enabled
echo $TRACING_ENABLED

# Check tracing endpoint
echo $TRACING_JAEGER_ENDPOINT

# Check sampling rate
echo $TRACING_SAMPLING_RATE
```

**Solutions:**

- **Enable tracing:**

  ```bash
  export TRACING_ENABLED=true
  ```

- **Check endpoint:**
  - Verify Jaeger endpoint is correct
  - Test connectivity to Jaeger

- **Increase sampling rate:**

  ```bash
  export TRACING_SAMPLING_RATE=1.0  # Sample all traces
  ```

## CSRF Issues

### CSRF Token Validation Failed

**Symptom:** POST/PUT/DELETE requests fail with 403 Forbidden

**Error Message:**

```json
{
  "error": {
    "code": "CSRF_VALIDATION_FAILED",
    "message": "CSRF token validation failed"
  }
}
```

**Solution:**

```bash
# Include CSRF token in request header
curl -X POST http://localhost:8000/v1/sessions \
  -H "Authorization: Bearer <token>" \
  -H "X-CSRF-Token: <csrf-token>" \
  -H "Content-Type: application/json" \
  -d '{"config":{}}'

# Or disable CSRF for testing (development only)
export CSRF_ENABLED=false
```

### Rate Limit Exceeded

**Symptom:** API returns 429 Too Many Requests

**Error Message:**

```json
{
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "Too many requests"
  }
}
```

**Solutions:**

- **Wait for rate limit window:**

```bash
# Check Retry-After header
curl -I http://localhost:8000/v1/sessions

# Wait specified seconds before retrying
```

- **Increase rate limit:**

```bash
# Update configuration
export RATE_LIMIT_PER_MINUTE=120

# Restart API
docker-compose restart api
```

- **Disable rate limiting (development only):**

```bash
export RATE_LIMIT_ENABLED=false
```

## Performance Issues

### Slow API Response Times

**Symptom:** API responses taking longer than expected

**Diagnosis:**

```bash
# Check response times
curl -w "@curl-format.txt" -o /dev/null -s http://localhost:8000/v1/sessions

# curl-format.txt:
# time_namelookup:  %{time_namelookup}\n
# time_connect:  %{time_connect}\n
# time_starttransfer:  %{time_starttransfer}\n
# time_total:  %{time_total}\n

# Check metrics
curl http://localhost:8000/metrics | grep http_request_duration

# Check database query times
# Enable query logging in PostgreSQL
```

**Solutions:**

- **Database optimization:**

```sql
-- Add indexes
CREATE INDEX idx_sessions_user_id ON sessions(user_id);

-- Analyze tables
ANALYZE sessions;
ANALYZE users;
```

- **Enable caching:**

```python
# Cache frequently accessed data in Redis
@cache(ttl=300)  # 5 minutes
def get_user(user_id: str):
    return db.query(User).filter(User.user_id == user_id).first()
```

- **Scale horizontally:**

```bash
# Add more API instances
docker-compose up -d --scale api=5

# Or Kubernetes
kubectl scale deployment apgi-api --replicas=5
```

- **Optimize queries:**

```python
# Use eager loading
sessions = db.query(Session).options(selectinload(Session.user)).all()

# Use pagination
sessions = db.query(Session).limit(100).offset(0).all()
```

### High Memory Usage

**Symptom:** API containers using excessive memory

**Diagnosis:**

```bash
# Check memory usage
docker stats

# Or Kubernetes
kubectl top pods

# Check Python memory usage
# Add to code:
import tracemalloc
tracemalloc.start()
# ... run code ...
snapshot = tracemalloc.take_snapshot()
top_stats = snapshot.statistics('lineno')
for stat in top_stats[:10]:
    print(stat)
```

**Solutions:**

- **Increase memory limits:**

```yaml
# docker-compose.yml
api:
  deploy:
    resources:
      limits:
        memory: 1G
```

- **Fix memory leaks:**

```python
# Close database sessions
with get_db_context() as db:
    # Use database
    pass

# Clear large objects
del large_object
import gc
gc.collect()
```

- **Reduce worker concurrency:**

```bash
# For Celery workers
export CELERY_WORKER_CONCURRENCY=2
```

### High CPU Usage

**Symptom:** API containers using excessive CPU

**Diagnosis:**

```bash
# Check CPU usage
docker stats

# Or Kubernetes
kubectl top pods

# Profile Python code
python -m cProfile -o profile.stats app/main.py
python -m pstats profile.stats
```

**Solutions:**

- **Optimize hot paths:**

```python
# Use list comprehensions instead of loops
result = [process(item) for item in items]

# Use generators for large datasets
def process_items():
    for item in items:
        yield process(item)
```

- **Scale horizontally:**

```bash
docker-compose up -d --scale api=5
kubectl scale deployment apgi-api --replicas=5
```

- **Increase CPU limits:**

```yaml
# docker-compose.yml
api:
  deploy:
    resources:
      limits:
        cpus: '2.0'
```

## Celery Worker Issues

### Celery Worker Not Starting

**Symptom:** Celery worker fails to start or exits immediately

**Diagnosis:**

```bash
# Check worker logs
docker-compose logs celery_worker
kubectl logs -l app=apgi-celery-worker

# Test Celery configuration
celery -A app.celery_app inspect ping
```

**Solutions:**

- **Fix broker connection:**

```bash
# Verify CELERY_BROKER_URL
echo $CELERY_BROKER_URL
# Should be: redis://redis:6379/1

# Test Redis connection
redis-cli -h redis-host -n 1 ping
```

- **Fix import errors:**

```bash
# Reinstall dependencies
pip install -r requirements.txt

# Rebuild Docker image
docker-compose build --no-cache celery_worker
```

### Tasks Not Executing

**Symptom:** Tasks submitted but not executed by workers

**Diagnosis:**

```bash
# Check worker status
celery -A app.celery_app inspect active
celery -A app.celery_app inspect reserved

# Check queue depth
redis-cli -h redis-host -n 1 LLEN celery

# Check worker logs
docker-compose logs -f celery_worker
```

**Solutions:**

- **Start workers:**

```bash
docker-compose up -d celery_worker
kubectl apply -f celery-deployment.yaml
```

- **Increase worker concurrency:**

```bash
export CELERY_WORKER_CONCURRENCY=4
docker-compose restart celery_worker
```

- **Check task routing:**

```python
# Verify task is registered
from app.celery_app import celery_app
print(celery_app.tasks.keys())
```

### Task Timeout Errors

**Symptom:** Tasks fail with timeout errors

**Error Message:**

```text
celery.exceptions.SoftTimeLimitExceeded
```

**Solutions:**

- **Increase task timeout:**

```bash
export CELERY_TASK_TIME_LIMIT=7200  # 2 hours
export CELERY_TASK_SOFT_TIME_LIMIT=6900  # 1h 55m
```

- **Optimize task:**

```python
# Break into smaller subtasks
@celery_app.task
def process_large_dataset(data):
    for chunk in chunks(data, 1000):
        process_chunk.delay(chunk)

@celery_app.task
def process_chunk(chunk):
    # Process smaller chunk
    pass
```

- **Use task retries:**

```python
@celery_app.task(bind=True, max_retries=3)
def my_task(self):
    try:
        # Task logic
        pass
    except Exception as exc:
        raise self.retry(exc=exc, countdown=60)
```

## CORS Issues

### CORS Policy Error

**Symptom:** Browser shows CORS policy error

**Error Message:**

```text
Access to fetch at 'http://api.example.com' from origin 'http://frontend.example.com'
has been blocked by CORS policy: No 'Access-Control-Allow-Origin' header is present
```

**Diagnosis:**

```bash
# Check CORS configuration
echo $CORS_ORIGINS

# Test CORS headers
curl -H "Origin: http://frontend.example.com" \
     -H "Access-Control-Request-Method: POST" \
     -H "Access-Control-Request-Headers: Content-Type" \
     -X OPTIONS \
     http://api.example.com/v1/sessions
```

**Solutions:**

- **Add origin to CORS_ORIGINS:**

```bash
export CORS_ORIGINS=http://frontend.example.com,http://another-domain.com
docker-compose restart api
```

- **Check protocol (http vs https):**

```bash
# Origin must match exactly including protocol
CORS_ORIGINS=https://frontend.example.com  # Not http://
```

- **Allow credentials:**

```bash
export CORS_ALLOW_CREDENTIALS=true
```

### Preflight Request Fails

**Symptom:** OPTIONS requests fail

**Diagnosis:**

```bash
# Test preflight request
curl -X OPTIONS http://localhost:8000/v1/sessions \
  -H "Origin: http://frontend.example.com" \
  -H "Access-Control-Request-Method: POST" \
  -v
```

**Solution:**

```bash
# Ensure CORS middleware is configured
# Should return 200 OK with CORS headers
```

## Logging and Monitoring

### Logs Not Appearing

**Symptom:** No logs visible in log aggregation system

**Diagnosis:**

```bash
# Check if API is logging to stdout
docker-compose logs api

# Check log level
echo $LOG_LEVEL
```

**Solutions:**

- **Set log level:**

```bash
export LOG_LEVEL=INFO
docker-compose restart api
```

- **Check log aggregation configuration:**

```bash
# Verify logs are being forwarded
# Check Filebeat/Fluentd/CloudWatch agent
```

### Metrics Not Collected

**Symptom:** Prometheus not scraping metrics

**Diagnosis:**

```bash
# Check metrics endpoint
curl http://localhost:8000/metrics

# Check Prometheus targets
# Visit http://prometheus:9090/targets
```

**Solutions:**

- **Verify metrics endpoint:**

```bash
# Should return Prometheus format metrics
curl http://localhost:8000/metrics | head
```

- **Update Prometheus configuration:**

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'apgi-api'
    static_configs:
      - targets: ['api:8000']
```

## Docker Issues

### Container Keeps Restarting

**Symptom:** Docker container restarts repeatedly

**Diagnosis:**

```bash
# Check container status
docker-compose ps

# Check restart count
docker inspect <container-id> | grep RestartCount

# Check logs
docker-compose logs --tail=100 api
```

**Solutions:**

- **Fix application error:**

```bash
# Check logs for error
docker-compose logs api | grep ERROR

# Fix configuration or code issue
```

- **Increase health check timeout:**

```yaml
# docker-compose.yml
api:
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
    interval: 30s
    timeout: 10s
    retries: 5
    start_period: 40s
```

### Volume Permission Issues

**Symptom:** Container can't write to mounted volumes

**Error Message:**

```text
PermissionError: [Errno 13] Permission denied
```

**Solutions:**

- **Fix volume permissions:**

```bash
# Change ownership
sudo chown -R 1000:1000 ./data

# Or run container as root (not recommended)
docker-compose run --user root api bash
```

- **Use named volumes:**

```yaml
# docker-compose.yml
volumes:
  postgres_data:
  redis_data:
```

## Kubernetes Issues

### Pod CrashLoopBackOff

**Symptom:** Pod keeps crashing and restarting

**Diagnosis:**

```bash
# Check pod status
kubectl get pods

# Check pod events
kubectl describe pod <pod-name>

# Check logs
kubectl logs <pod-name>
kubectl logs <pod-name> --previous  # Previous container logs
```

**Solutions:**

- **Fix application error:**

```bash
# Check logs for error
kubectl logs <pod-name> | grep ERROR
```

- **Increase resource limits:**

```yaml
# deployment.yaml
resources:
  limits:
    memory: "1Gi"
    cpu: "1000m"
  requests:
    memory: "512Mi"
    cpu: "500m"
```

- **Fix liveness probe:**

```yaml
# deployment.yaml
livenessProbe:
  httpGet:
    path: /health/live
    port: 8000
  initialDelaySeconds: 30  # Increase if app takes time to start
  periodSeconds: 30
```

### ImagePullBackOff

**Symptom:** Kubernetes can't pull Docker image

**Diagnosis:**

```bash
# Check pod events
kubectl describe pod <pod-name>

# Check image name
kubectl get pod <pod-name> -o jsonpath='{.spec.containers[0].image}'
```

**Solutions:**

- **Fix image name:**

```yaml
# deployment.yaml
image: your-registry.com/apgi-api:1.1.0  # Correct registry and tag
```

- **Add image pull secret:**

```bash
# Create secret
kubectl create secret docker-registry regcred \
  --docker-server=your-registry.com \
  --docker-username=<username> \
  --docker-password=<password>

# Add to deployment
spec:
  imagePullSecrets:
  - name: regcred
```

## Network Issues

### Cannot Reach API

**Symptom:** API not accessible from outside

**Diagnosis:**

```bash
# Check if API is listening
netstat -tlnp | grep 8000

# Check firewall rules
iptables -L

# Test from inside container
docker-compose exec api curl http://localhost:8000/health
```

**Solutions:**

- **Check port binding:**

```yaml
# docker-compose.yml
api:
  ports:
    - "8000:8000"  # host:container
```

- **Check firewall:**

```bash
# Allow port 8000
sudo ufw allow 8000
sudo firewall-cmd --add-port=8000/tcp --permanent
```

- **Check load balancer:**

```bash
# Verify load balancer is routing to API
curl -v http://load-balancer-url/health
```

### DNS Resolution Failures

**Symptom:** Container can't resolve hostnames

**Error Message:**

```text
getaddrinfo: Name or service not known
```

**Solutions:**

- **Use IP addresses:**

```bash
# Instead of hostname
DATABASE_URL=postgresql://user:pass@10.0.1.5:5432/db
```

- **Fix DNS configuration:**

```yaml
# docker-compose.yml
api:
  dns:
    - 8.8.8.8
    - 8.8.4.4
```

- **Use Docker network:**

```bash
# Ensure services are on same network
docker network ls
docker network inspect <network-name>
```

## Data Issues

### Data Inconsistency

**Symptom:** Data in database doesn't match expected state

**Diagnosis:**

```sql
-- Check for orphaned records
SELECT * FROM sessions WHERE user_id NOT IN (SELECT user_id FROM users);

-- Check for duplicate records
SELECT session_id, COUNT(*) FROM sessions
GROUP BY session_id HAVING COUNT(*) > 1;

-- Check data integrity
SELECT * FROM sessions WHERE created_at > updated_at;
```

**Solutions:**

- **Clean up orphaned records:**

```sql
DELETE FROM sessions WHERE user_id NOT IN (SELECT user_id FROM users);
```

- **Fix duplicate records:**

```sql
-- Keep only the latest record
DELETE FROM sessions
WHERE id NOT IN (
  SELECT MAX(id) FROM sessions GROUP BY session_id
);
```

- **Add constraints:**

```sql
-- Add foreign key constraint
ALTER TABLE sessions ADD CONSTRAINT fk_user
  FOREIGN KEY (user_id) REFERENCES users(user_id);

-- Add unique constraint
ALTER TABLE sessions ADD CONSTRAINT unique_session_id UNIQUE (session_id);
```

### Data Migration Issues

**Symptom:** Data lost or corrupted after migration

**Solutions:**

- **Restore from backup:**

```bash
psql -h postgres-host -U apgi_user apgi_api < backup_20240115.sql
```

- **Verify data integrity:**

```sql
-- Check record counts
SELECT COUNT(*) FROM users;
SELECT COUNT(*) FROM sessions;

-- Check sample records
SELECT * FROM users LIMIT 10;
SELECT * FROM sessions LIMIT 10;
```

- **Re-run migration:**

```bash
# Rollback
alembic downgrade -1

# Reapply
alembic upgrade head
```

## Getting Help

### Before Asking for Help

- **Check this troubleshooting guide**
- **Check logs for error messages**
- **Check health endpoints**
- **Verify configuration**
- **Try restarting services**

### Information to Provide

When asking for help, include:

- **Error message** (full stack trace)
- **Logs** (relevant sections)
- **Configuration** (sanitized, no secrets)
- **Environment** (development/staging/production)
- **Steps to reproduce**
- **What you've tried**

### Contact Information

- **Documentation:** [README.md](../README.md)
- **Configuration Guide:** [CONFIGURATION.md](CONFIGURATION.md)
- **Deployment Guide:** [DEPLOYMENT.md](DEPLOYMENT.md)
- **DevOps Team:** [devops@example.com](mailto:devops@example.com)
- **On-Call Engineer:** [oncall@example.com](mailto:oncall@example.com)
- **Slack Channel:** #api-support

### Escalation

**Severity Levels:**

- **P0 (Critical):** API completely down, data loss
  - Contact: On-call engineer immediately
  - Response: 15 minutes

- **P1 (High):** Major functionality broken, high error rate
  - Contact: DevOps team
  - Response: 1 hour

- **P2 (Medium):** Minor functionality broken, workaround available
  - Contact: DevOps team
  - Response: 4 hours

- **P3 (Low):** Cosmetic issues, feature requests
  - Contact: DevOps team
  - Response: 1 business day

## Debugging Tips

### Enable Debug Logging

```bash
export LOG_LEVEL=DEBUG
docker-compose restart api
```

### Use Python Debugger

```python
# Add to code
import pdb; pdb.set_trace()

# Or use ipdb
import ipdb; ipdb.set_trace()
```

### Profile Performance

```bash
# Profile API endpoint
python -m cProfile -o profile.stats -m uvicorn app.main:app

# Analyze profile
python -m pstats profile.stats
```

### Test Database Queries

```python
# Enable SQLAlchemy query logging
import logging
logging.basicConfig()
logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)
```

### Monitor in Real-Time

```bash
# Watch logs
watch -n 1 'docker-compose logs --tail=20 api'

# Watch metrics
watch -n 1 'curl -s http://localhost:8000/metrics | grep http_requests_total'

# Watch resource usage
watch -n 1 'docker stats --no-stream'
```
