# APGI API Operations Runbook

This document provides operational procedures for deploying, maintaining, and troubleshooting the APGI API in production environments.

## Pre-Flight Checklist

Complete this checklist before every deployment to ensure system readiness and minimize deployment risks.

### Infrastructure Requirements

- [ ] Kubernetes cluster accessible and healthy
- [ ] Database (PostgreSQL) accessible and healthy
- [ ] Redis cache accessible and healthy
- [ ] SMTP server accessible (if email features enabled)
- [ ] External services (Stripe, payment providers) accessible
- [ ] Load balancer configured with proper SSL certificates
- [ ] Monitoring systems (Prometheus, Grafana) operational
- [ ] Log aggregation system operational

### Application Prerequisites

- [ ] Docker images built and pushed to registry
- [ ] Database migrations tested in staging
- [ ] Environment variables configured
- [ ] Secrets mounted correctly
- [ ] Service mesh (Istio/Linkerd) configured
- [ ] Ingress rules updated for new routes
- [ ] API gateway configured with proper routing

### Testing and Validation

- [ ] Unit tests passing (coverage > 90%)
- [ ] Integration tests passing
- [ ] End-to-end tests passing in staging
- [ ] Performance tests completed (response time < 200ms)
- [ ] Security scan completed (no critical vulnerabilities)
- [ ] Accessibility audit completed (WCAG 2.1 AA compliant)

### Operational Readiness

- [ ] Runbooks updated for new features
- [ ] Monitoring dashboards updated
- [ ] Alert thresholds configured
- [ ] On-call rotation updated
- [ ] Communication plan prepared
- [ ] Rollback plan documented and tested

### Team Coordination

- [ ] Deployment window scheduled (low-traffic hours)
- [ ] Stakeholders notified
- [ ] Rollback procedures communicated
- [ ] Post-deployment monitoring plan established

## Secrets Rotation SOP

Regular rotation of secrets is critical for maintaining security posture.

### API Keys Rotation

```bash
# List all active API keys older than 90 days
kubectl exec deployment/apgi-api -- python -c "
from app.database.connection import get_db
from app.database.models import APIKey
from datetime import datetime, timedelta

db = next(get_db())
old_keys = db.query(APIKey).filter(
    APIKey.created_at < datetime.utcnow() - timedelta(days=90),
    APIKey.is_active == True
).all()

for key in old_keys:
    print(f'Key {key.key_id}: {key.created_at}')
"

# Rotate individual API key
curl -X POST https://api.apgi.com/v1/api-keys/{key_id}/rotate \
  -H "Authorization: Bearer {admin_token}"

# Notify key owners
# Send email notification to key owner with new key
```

### Database Credentials Rotation

```bash
# Update Kubernetes secret
kubectl create secret generic apgi-db-secret \
  --from-literal=password=$(openssl rand -base64 32) \
  --dry-run=client -o yaml | kubectl apply -f -

# Update database user password
kubectl exec -it deployment/postgres -- psql -c "
ALTER USER apgi PASSWORD '$(kubectl get secret apgi-db-secret -o jsonpath='{.data.password}' | base64 -d)';
"

# Restart application pods to pick up new credentials
kubectl rollout restart deployment/apgi-api
```

### JWT Secret Rotation

```bash
# Generate new JWT secret
NEW_JWT_SECRET=$(openssl rand -hex 32)

# Update Kubernetes secret
kubectl patch secret apgi-secrets \
  -p "{\"data\":{\"jwt-secret\":\"$(echo -n $NEW_JWT_SECRET | base64)\"}}"

# Wait for secret propagation
sleep 30

# Restart application pods
kubectl rollout restart deployment/apgi-api

# Verify new tokens work
curl -H "Authorization: Bearer {test_token}" https://api.apgi.com/v1/users/me
```

### Certificate Rotation

```bash
# Renew SSL certificate via cert-manager
kubectl certificate approve apgi-tls-cert

# Verify certificate renewal
kubectl get certificate apgi-tls-cert

# Update DNS CAA records if necessary
# CAA records should allow Let's Encrypt
```

## Automated Rollback Procedures

Follow these steps to rollback a deployment in case of issues.

### Automated Rollback

```bash
# Switch traffic back to previous version
kubectl patch service apgi-api -p '{"spec":{"selector":{"version":"green"}}}'

# Scale down blue environment
kubectl scale deployment apgi-api-blue --replicas=0
```

### Database Rollback

```bash
# Identify migration to rollback
kubectl exec deployment/apgi-api -- python -m alembic current

# Rollback specific migration
kubectl exec deployment/apgi-api -- python -m alembic downgrade {revision_id}

# Verify data integrity
kubectl exec deployment/apgi-api -- python -c "
# Run data integrity checks
from app.database.connection import get_db
# Add integrity check queries here
"
```

### Manual Rollback Steps

1. **Assess Impact**: Determine which systems are affected
2. **Stop Traffic**: Switch load balancer to maintenance page
3. **Restore Backup**: If data corruption occurred, restore from backup
4. **Rollback Code**: Deploy previous version
5. **Verify Functionality**: Run health checks and smoke tests
6. **Resume Traffic**: Switch load balancer back to application

### Rollback Validation

```bash
# Verify application health
curl -f https://api.apgi.com/health

# Check error rates
kubectl logs --since=1h deployment/apgi-api | grep ERROR | wc -l

# Validate core functionality
# Run critical user journey tests
```

## Backup and Restore

### Database Backup and Restore (Kubernetes)

```bash
# Create database backup
kubectl exec deployment/postgres -- pg_dump -U apgi apgi_db > backup_$(date +%Y%m%d_%H%M%S).sql

# Upload to secure storage
aws s3 cp backup_$(date +%Y%m%d_%H%M%S).sql s3://apgi-backups/database/

# Clean up old backups (keep last 30 days)
aws s3 ls s3://apgi-backups/database/ | awk '$1 < "'$(date -d '30 days ago' +%Y-%m-%d)'"' | xargs -I {} aws s3 rm s3://apgi-backups/database/{}
```

### Database Restore

```bash
# Stop application to prevent writes during restore
kubectl scale deployment/apgi-api --replicas=0

# Restore from backup
kubectl exec -i deployment/postgres -- psql -U apgi apgi_db < backup_file.sql

# Verify restore success
kubectl exec deployment/postgres -- psql -U apgi -c "SELECT COUNT(*) FROM users;"

# Restart application
kubectl scale deployment/apgi-api --replicas=3
```

### Application Configuration Backup

```bash
# Backup Kubernetes resources
kubectl get all,configmaps,secrets,ingresses -l app=apgi -o yaml > k8s_backup_$(date +%Y%m%d).yaml

# Backup Helm releases
helm list -A > helm_releases_$(date +%Y%m%d).txt
```

### Point-in-Time Recovery

```bash
# Enable WAL archiving in PostgreSQL
# Configure pgBackRest or similar for PITR

# Perform PITR restore
kubectl exec deployment/postgres -- pgbackrest restore --type=time --target="2024-01-15 10:30:00"

# Verify data consistency
kubectl exec deployment/postgres -- psql -U apgi -c "
SELECT max(created_at) FROM audit_logs;
SELECT count(*) FROM users;
"
```

## Emergency Contacts

- **On-call Engineer**: +1-555-0123 (PagerDuty)
- **DevOps Lead**: <devops@apgi.com>
- **Security Team**: <security@apgi.com>
- **Database Admin**: <dba@apgi.com>

### Escalation Procedure

1. **Level 1**: On-call engineer investigates for 15 minutes
2. **Level 2**: Escalate to DevOps lead if unresolved after 30 minutes
3. **Level 3**: Escalate to engineering manager if unresolved after 1 hour
4. **Level 4**: Declare incident and notify all stakeholders

---

**Last Updated**: January 2025
**Version**: 2.0
**Authors**: DevOps Team

## Table of Contents

- [Prerequisites](#prerequisites)
- [Environment Configuration](#environment-configuration)
- [Docker Deployment](#docker-deployment)
- [Kubernetes Deployment](#kubernetes-deployment)
- [Database Setup](#database-setup)
- [Celery Worker Setup](#celery-worker-setup)
- [Production Checklist](#production-checklist)
- [Monitoring and Maintenance](#monitoring-and-maintenance)
- [Scaling](#scaling)
- [Rollback and Recovery Procedures](#rollback-and-recovery-procedures)

## Prerequisites

### Prerequisites - Infrastructure Requirements

**Minimum Requirements (Development/Staging):**

- 2 CPU cores
- 4 GB RAM
- 20 GB disk space
- PostgreSQL 14+
- Redis 7+

**Recommended Requirements (Production):**

- 4+ CPU cores per API instance
- 8+ GB RAM per API instance
- 100+ GB disk space
- PostgreSQL 14+ with replication
- Redis 7+ with persistence
- Load balancer (nginx, HAProxy, or cloud provider)

### Software Requirements

- Docker 20.10+ and Docker Compose 2.0+ (for Docker deployment)
- Kubernetes 1.24+ (for Kubernetes deployment)
- Python 3.11+ (for manual deployment)
- PostgreSQL client tools (for database management)

### Network Requirements

- Outbound internet access for package installation
- Inbound access on port 8000 (API)
- Access to PostgreSQL (default port 5432)
- Access to Redis (default port 6379)

## Environment Configuration

### Required Environment Variables

Create a `.env` file with the following variables:

```bash
# Environment
ENVIRONMENT=production  # development, staging, or production

# API Settings
API_TITLE=APGI Standalone API
API_VERSION=1.1.0
HOST=0.0.0.0
PORT=8000

# Database Configuration
DATABASE_URL=postgresql://username:password@postgres-host:5432/apgi_api
# For production, use connection pooling:
# DATABASE_URL=postgresql://username:password@postgres-host:5432/apgi_api?pool_size=10&max_overflow=20

# Redis Configuration
REDIS_URL=redis://redis-host:6379/0

# Celery Configuration
CELERY_BROKER_URL=redis://redis-host:6379/1
CELERY_RESULT_BACKEND=redis://redis-host:6379/2

# Authentication
JWT_SECRET_KEY=your-secure-secret-key-minimum-32-characters-long
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7

# CORS Configuration
CORS_ORIGINS=https://your-frontend-domain.com,https://another-domain.com
CORS_ALLOW_CREDENTIALS=true

# Rate Limiting
RATE_LIMIT_ENABLED=true
RATE_LIMIT_PER_MINUTE=60

# Logging
LOG_LEVEL=INFO  # DEBUG, INFO, WARNING, ERROR, CRITICAL

# Alerting Configuration (Optional)
ALERT_WEBHOOK_URLS=https://hooks.slack.com/services/XXX/YYY/ZZZ
ALERT_SLACK_WEBHOOK_URLS=https://hooks.slack.com/services/XXX/YYY/ZZZ
ALERT_PAGERDUTY_INTEGRATION_KEYS=abc123def456
ALERT_TEAMS_WEBHOOK_URLS=https://outlook.office.com/webhook/XXX/YYY/ZZZ
ALERT_ERROR_RATE_THRESHOLD=10
ALERT_ERROR_RATE_WINDOW_MINUTES=1

# Monitoring Configuration
METRICS_ENABLED=true
METRICS_PATH=/metrics

# Distributed Tracing Configuration (Optional)
TRACING_ENABLED=false
TRACING_SERVICE_NAME=apgi-api
TRACING_JAEGER_ENDPOINT=http://jaeger:14268/api/traces
TRACING_OTLP_ENDPOINT=grpc://jaeger:4317
TRACING_SAMPLING_RATE=1.0
TRACING_CONSOLE_EXPORTER=false
```

### Security Considerations

**Production Environment:**

- `JWT_SECRET_KEY` must be at least 32 characters and cryptographically random
- `CORS_ORIGINS` must be explicitly configured (no wildcards with credentials)
- `DATABASE_URL` should use SSL/TLS connections
- Never commit `.env` files to version control
- Use secrets management (AWS Secrets Manager, HashiCorp Vault, etc.)

**Generate a secure JWT secret:**

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Docker Deployment

### Building the Docker Image

**Production Image:**

```bash
cd standalone-api
docker build -t apgi-api:latest -f deployment/Dockerfile .
```

**Development Image:**

```bash
docker build -t apgi-api:dev -f deployment/Dockerfile.dev .
```

### Docker Compose Deployment

**1. Create environment file:**

```bash
cp .env.production .env
# Edit .env with your configuration
```

**2. Start all services:**

```bash
docker-compose -f deployment/docker-compose.yml up -d
```

This starts:

- PostgreSQL database
- Redis cache
- API server (3 instances behind load balancer)
- Celery worker (2 instances)

**3. Run database migrations:**

```bash
docker-compose -f deployment/docker-compose.yml exec api alembic upgrade head
```

**4. Verify deployment:**

```bash
# Check health
curl http://localhost:8000/health

# Check readiness
curl http://localhost:8000/health/ready

# View logs
docker-compose -f deployment/docker-compose.yml logs -f api
```

**5. Stop services:**

```bash
docker-compose -f deployment/docker-compose.yml down
```

**6. Stop and remove volumes (WARNING: deletes data):**

```bash
docker-compose -f deployment/docker-compose.yml down -v
```

### Docker Compose Production Configuration

For production, use `docker-compose.prod.yml`:

```bash
# Start production stack
docker-compose -f deployment/docker-compose.prod.yml up -d

# Scale API instances
docker-compose -f deployment/docker-compose.prod.yml up -d --scale api=5

# Scale Celery workers
docker-compose -f deployment/docker-compose.prod.yml up -d --scale celery_worker=3
```

## Kubernetes Deployment

### Kubernetes Prerequisites

- Kubernetes cluster (1.24+)
- kubectl configured to access your cluster
- Container registry (Docker Hub, ECR, GCR, etc.)

### Step 1: Push Docker Image to Registry

```bash
# Tag image
docker tag apgi-api:latest your-registry.com/apgi-api:1.1.0

# Push to registry
docker push your-registry.com/apgi-api:1.1.0
```

### Step 2: Create Kubernetes Secrets

```bash
# Create secret for database credentials
kubectl create secret generic apgi-secrets \
  --from-literal=database-url='postgresql://user:pass@postgres:5432/apgi_api' \
  --from-literal=redis-url='redis://redis:6379/0' \
  --from-literal=jwt-secret='your-secure-jwt-secret-key'

# Verify secret
kubectl get secrets apgi-secrets
```

### Step 3: Deploy PostgreSQL and Redis

**PostgreSQL Deployment:**

```yaml
# postgres-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres
spec:
  replicas: 1
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      containers:
      - name: postgres
        image: postgres:14-alpine
        env:
        - name: POSTGRES_DB
          value: apgi_api
        - name: POSTGRES_USER
          value: apgi_user
        - name: POSTGRES_PASSWORD
          valueFrom:
            secretKeyRef:
              name: apgi-secrets
              key: postgres-password
        ports:
        - containerPort: 5432
        volumeMounts:
        - name: postgres-storage
          mountPath: /var/lib/postgresql/data
      volumes:
      - name: postgres-storage
        persistentVolumeClaim:
          claimName: postgres-pvc
---
apiVersion: v1
kind: Service
metadata:
  name: postgres
spec:
  selector:
    app: postgres
  ports:
  - port: 5432
    targetPort: 5432
```

**Redis Deployment:**

```yaml
# redis-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redis
spec:
  replicas: 1
  selector:
    matchLabels:
      app: redis
  template:
    metadata:
      labels:
        app: redis
    spec:
      containers:
      - name: redis
        image: redis:7-alpine
        ports:
        - containerPort: 6379
        volumeMounts:
        - name: redis-storage
          mountPath: /data
      volumes:
      - name: redis-storage
        persistentVolumeClaim:
          claimName: redis-pvc
---
apiVersion: v1
kind: Service
metadata:
  name: redis
spec:
  selector:
    app: redis
  ports:
  - port: 6379
    targetPort: 6379
```

### Step 4: Deploy API

```yaml
# api-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: apgi-api
  labels:
    app: apgi-api
spec:
  replicas: 3
  selector:
    matchLabels:
      app: apgi-api
  template:
    metadata:
      labels:
        app: apgi-api
    spec:
      containers:
      - name: api
        image: your-registry.com/apgi-api:1.1.0
        ports:
        - containerPort: 8000
        env:
        - name: ENVIRONMENT
          value: "production"
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: apgi-secrets
              key: database-url
        - name: REDIS_URL
          valueFrom:
            secretKeyRef:
              name: apgi-secrets
              key: redis-url
        - name: JWT_SECRET_KEY
          valueFrom:
            secretKeyRef:
              name: apgi-secrets
              key: jwt-secret
        - name: CELERY_BROKER_URL
          value: "redis://redis:6379/1"
        - name: CELERY_RESULT_BACKEND
          value: "redis://redis:6379/2"
        - name: CORS_ORIGINS
          value: "https://your-frontend.com"
        livenessProbe:
          httpGet:
            path: /health/live
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 30
          timeoutSeconds: 5
        readinessProbe:
          httpGet:
            path: /health/ready
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 10
          timeoutSeconds: 5
        resources:
          requests:
            memory: "256Mi"
            cpu: "250m"
          limits:
            memory: "512Mi"
            cpu: "500m"
---
apiVersion: v1
kind: Service
metadata:
  name: apgi-api
spec:
  selector:
    app: apgi-api
  ports:
  - port: 80
    targetPort: 8000
  type: LoadBalancer
```

### Step 5: Deploy Celery Workers

```yaml
# celery-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: apgi-celery-worker
spec:
  replicas: 2
  selector:
    matchLabels:
      app: apgi-celery-worker
  template:
    metadata:
      labels:
        app: apgi-celery-worker
    spec:
      containers:
      - name: worker
        image: your-registry.com/apgi-api:1.1.0
        command: ["celery", "-A", "app.celery_app", "worker", "--loglevel=info"]
        env:
        - name: ENVIRONMENT
          value: "production"
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: apgi-secrets
              key: database-url
        - name: REDIS_URL
          valueFrom:
            secretKeyRef:
              name: apgi-secrets
              key: redis-url
        - name: CELERY_BROKER_URL
          value: "redis://redis:6379/1"
        - name: CELERY_RESULT_BACKEND
          value: "redis://redis:6379/2"
        resources:
          requests:
            memory: "512Mi"
            cpu: "500m"
          limits:
            memory: "1Gi"
            cpu: "1000m"
```

### Step 6: Apply Kubernetes Manifests

```bash
# Create persistent volumes (if not using dynamic provisioning)
kubectl apply -f postgres-pvc.yaml
kubectl apply -f redis-pvc.yaml

# Deploy data services
kubectl apply -f postgres-deployment.yaml
kubectl apply -f redis-deployment.yaml

# Wait for data services to be ready
kubectl wait --for=condition=ready pod -l app=postgres --timeout=300s
kubectl wait --for=condition=ready pod -l app=redis --timeout=300s

# Deploy API
kubectl apply -f api-deployment.yaml

# Deploy Celery workers
kubectl apply -f celery-deployment.yaml

# Verify deployments
kubectl get deployments
kubectl get pods
kubectl get services
```

### Step 7: Run Database Migrations

```bash
# Get API pod name
API_POD=$(kubectl get pods -l app=apgi-api -o jsonpath='{.items[0].metadata.name}')

# Run migrations
kubectl exec -it $API_POD -- alembic upgrade head
```

### Step 8: Configure Ingress (Optional)

```yaml
# ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: apgi-api-ingress
  annotations:
    kubernetes.io/ingress.class: nginx
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  tls:
  - hosts:
    - api.yourdomain.com
    secretName: apgi-api-tls
  rules:
  - host: api.yourdomain.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: apgi-api
            port:
              number: 80
```

```bash
kubectl apply -f ingress.yaml
```

## Database Setup

### Initial Database Creation

**Using PostgreSQL client:**

```bash
# Connect to PostgreSQL
psql -h postgres-host -U postgres

# Create database
CREATE DATABASE apgi_api;

# Create user
CREATE USER apgi_user WITH PASSWORD 'secure_password';

# Grant privileges
GRANT ALL PRIVILEGES ON DATABASE apgi_api TO apgi_user;

# Exit
\q
```

### Running Migrations

**Docker:**

```bash
docker-compose exec api alembic upgrade head
```

**Kubernetes:**

```bash
kubectl exec -it <api-pod-name> -- alembic upgrade head
```

**Manual:**

```bash
cd standalone-api
alembic upgrade head
```

### Migration Management

**View migration history:**

```bash
alembic history
```

**View current version:**

```bash
alembic current
```

**Rollback one migration:**

```bash
alembic downgrade -1
```

**Rollback to specific version:**

```bash
alembic downgrade <revision_id>
```

**Create new migration:**

```bash
alembic revision --autogenerate -m "Description of changes"
```

### Database Backup and Restore (Manual)

**Backup:**

```bash
pg_dump -h postgres-host -U apgi_user apgi_api > backup_$(date +%Y%m%d_%H%M%S).sql
```

**Restore:**

```bash
psql -h postgres-host -U apgi_user apgi_api < backup_20240115_120000.sql
```

**Automated backups (cron):**

```bash
# Add to crontab
0 2 * * * pg_dump -h postgres-host -U apgi_user apgi_api > /backups/apgi_api_$(date +\%Y\%m\%d).sql
```

## Celery Worker Setup

### Starting Celery Workers

**Docker:**

```bash
docker-compose -f deployment/docker-compose.yml up -d celery_worker
```

**Kubernetes:**

```bash
kubectl apply -f celery-deployment.yaml
```

**Manual:**

```bash
celery -A app.celery_app worker --loglevel=info
```

### Scaling Workers

**Docker Compose:**

```bash
docker-compose -f deployment/docker-compose.yml up -d --scale celery_worker=5
```

**Kubernetes:**

```bash
kubectl scale deployment apgi-celery-worker --replicas=5
```

### Monitoring Workers

**View worker status:**

```bash
celery -A app.celery_app inspect active
celery -A app.celery_app inspect stats
```

**View task queue:**

```bash
celery -A app.celery_app inspect reserved
```

### Worker Configuration

Workers can be configured via environment variables:

```bash
# Concurrency (number of worker processes)
CELERY_WORKER_CONCURRENCY=4

# Task time limits
CELERY_TASK_TIME_LIMIT=3600        # 1 hour hard limit
CELERY_TASK_SOFT_TIME_LIMIT=3300   # 55 minute soft limit

# Prefetch multiplier (tasks to prefetch per worker)
CELERY_WORKER_PREFETCH_MULTIPLIER=4
```

## Production Checklist

Before deploying to production, verify:

### Security

- [ ] `JWT_SECRET_KEY` is cryptographically random (32+ characters)
- [ ] `CORS_ORIGINS` is explicitly configured (no wildcards)
- [ ] Database uses SSL/TLS connections
- [ ] Redis uses password authentication
- [ ] Secrets are stored in secrets manager (not in code)
- [ ] API is behind HTTPS/TLS termination
- [ ] Rate limiting is enabled
- [ ] CSRF protection is enabled

### Configuration

- [ ] `ENVIRONMENT=production`
- [ ] `LOG_LEVEL=INFO` or `WARNING`
- [ ] Database connection pooling is configured
- [ ] Redis persistence is enabled (AOF or RDB)
- [ ] Celery result expiration is set
- [ ] All required environment variables are set

### Infrastructure

- [ ] PostgreSQL has automated backups
- [ ] Redis has persistence enabled
- [ ] Load balancer is configured with health checks
- [ ] Monitoring and alerting are set up
- [ ] Log aggregation is configured
- [ ] Resource limits are set (CPU, memory)

### Testing

- [ ] Health checks return 200 OK
- [ ] Readiness checks verify all dependencies
- [ ] Authentication flow works end-to-end
- [ ] Session creation and management work
- [ ] Task submission and retrieval work
- [ ] Database migrations are up to date

### Documentation

- [ ] Deployment runbook is complete
- [ ] Rollback procedures are documented
- [ ] On-call contacts are documented
- [ ] Monitoring dashboards are created

## Monitoring and Maintenance

### Health Checks

**Basic health:**

```bash
curl http://api-host:8000/health
```

**Readiness (checks dependencies):**

```bash
curl http://api-host:8000/health/ready
```

**Liveness:**

```bash
curl http://api-host:8000/health/live
```

### Metrics

**Prometheus metrics:**

```bash
curl http://api-host:8000/metrics
```

Key metrics to monitor:

- `http_requests_total` - Total request count
- `http_request_duration_seconds` - Request latency
- `http_requests_in_progress` - Active requests
- `database_connections_active` - Database connection pool usage
- `celery_tasks_total` - Task execution count
- `celery_task_duration_seconds` - Task execution time

### Log Aggregation

Logs are written to stdout in JSON format. Configure log aggregation:

**ELK Stack:**

- Filebeat → Logstash → Elasticsearch → Kibana

**Cloud Providers:**

- AWS: CloudWatch Logs
- GCP: Cloud Logging
- Azure: Azure Monitor

**Example log query (Elasticsearch):**

```json
{
  "query": {
    "bool": {
      "must": [
        { "match": { "level": "ERROR" }},
        { "range": { "timestamp": { "gte": "now-1h" }}}
      ]
    }
  }
}
```

### Alerting

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

Alerting is configured via environment variables (see Environment Configuration section above).

**Configure alerts for:**

- Error rate > 10 errors/minute for 5 minutes
- Response time p95 > 1000ms for 5 minutes
- Health check failures
- Database connection failures
- Redis connection failures
- Celery worker failures
- Disk space < 10%
- Memory usage > 90%

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

See the Distributed Tracing Configuration section in Environment Variables above.

**Instrumented Components:**

- FastAPI request/response cycles
- SQLAlchemy database queries
- Redis cache operations
- HTTP client requests
- Async task execution

**Viewing Traces:**

- Jaeger UI: Navigate to your Jaeger instance
- Select service: `apgi-api`
- Search by trace ID or operation name
- View trace timeline and spans

## Scaling

### Horizontal Scaling

**API Instances:**

```bash
# Docker Compose
docker-compose up -d --scale api=5

# Kubernetes
kubectl scale deployment apgi-api --replicas=5
```

**Celery Workers:**

```bash
# Docker Compose
docker-compose up -d --scale celery_worker=3

# Kubernetes
kubectl scale deployment apgi-celery-worker --replicas=3
```

### Auto-scaling (Kubernetes)

```yaml
# api-hpa.yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: apgi-api-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: apgi-api
  minReplicas: 2
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: 80
```

```bash
kubectl apply -f api-hpa.yaml
```

### Database Scaling

**Read Replicas:**

- Configure PostgreSQL streaming replication
- Route read queries to replicas
- Keep writes on primary

**Connection Pooling:**

- Use PgBouncer for connection pooling
- Configure pool size based on load

### Redis Scaling

**Redis Cluster:**

- Deploy Redis Cluster for horizontal scaling
- Configure sharding for large datasets

**Redis Sentinel:**

- Deploy Redis Sentinel for high availability
- Automatic failover on primary failure

## Rollback and Recovery Procedures

### Application Rollback

**Docker:**

```bash
# Tag current version
docker tag apgi-api:latest apgi-api:backup

# Pull previous version
docker pull your-registry.com/apgi-api:1.1.0

# Restart with previous version
docker-compose down
docker-compose up -d
```

**Kubernetes:**

```bash
# Rollback to previous deployment
kubectl rollout undo deployment/apgi-api

# Rollback to specific revision
kubectl rollout undo deployment/apgi-api --to-revision=2

# View rollout history
kubectl rollout history deployment/apgi-api
```

### Database Recovery

**Rollback one migration:**

```bash
alembic downgrade -1
```

**Rollback to specific version:**

```bash
alembic downgrade <revision_id>
```

**Restore from backup:**

```bash
# Stop API
docker-compose stop api

# Restore database
psql -h postgres-host -U apgi_user apgi_api < backup_20240115_120000.sql

# Start API
docker-compose start api
```

### Emergency Procedures

**Complete system rollback:**

1. Stop all API instances
2. Stop all Celery workers
3. Restore database from backup
4. Deploy previous application version
5. Verify health checks
6. Gradually restore traffic

**Partial rollback (canary):**

1. Deploy previous version alongside current
2. Route 10% traffic to previous version
3. Monitor error rates and performance
4. Gradually increase traffic to previous version
5. Decommission current version when stable

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for detailed troubleshooting guidance.

### Quick Diagnostics

**Check API logs:**

```bash
# Docker
docker-compose logs -f api

# Kubernetes
kubectl logs -f deployment/apgi-api
```

**Check database connectivity:**

```bash
psql -h postgres-host -U apgi_user apgi_api -c "SELECT 1"
```

**Check Redis connectivity:**

```bash
redis-cli -h redis-host ping
```

**Check Celery workers:**

```bash
celery -A app.celery_app inspect ping
```

## Support

For issues or questions:

- Check [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- Review logs for error messages
- Check health endpoints for dependency status
- Contact DevOps team: [devops@example.com](mailto:devops@example.com)
