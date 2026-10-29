# HANDS ON: Getting APGI Running

## Overview

In this section, you'll get the APGI API running locally and explore it interactively. You don't need to understand the code yet—just see it in action.

**Time**: 1-2 hours (mostly waiting for setup and Docker)

## Part 1: Environment Setup (30 minutes)

### Step 1a: Prerequisites Check

```bash
# Check Python version (need 3.8+)
python3 --version

# Check git is installed
git --version

# Check Docker is installed (optional but recommended)
docker --version
docker-compose --version
```

If any are missing, install them before continuing.

### Step 1b: Clone and Navigate

```bash
# If you haven't already, clone the repository
git clone https://github.com/apgiframework/apgi-api.git
cd apgi-api

# Verify you're on the right branch (should be already)
git branch
# Should show: * claude/project-to-course-Tdx5q
```

### Step 1c: Create Virtual Environment

```bash
# Create virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate  # On macOS/Linux
# or on Windows:
# venv\Scripts\activate

# Verify it's active (prompt should show (venv))
```

### Step 1d: Install Dependencies

```bash
# Install packages
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Verify key packages are installed
python -c "import fastapi; print(f'FastAPI {fastapi.__version__}')"
python -c "import sqlalchemy; print(f'SQLAlchemy {sqlalchemy.__version__}')"
python -c "import pytest; print(f'pytest {pytest.__version__}')"
```

### Step 1e: Generate Secure Keys

```bash
# Generate JWT secret (copy the output)
python -c "import secrets; print('JWT_SECRET_KEY=' + secrets.token_urlsafe(32))"

# Generate cursor signing key (copy the output)
python -c "import secrets; print('CURSOR_SIGNING_KEY=' + secrets.token_urlsafe(32))"
```

### Step 1f: Create .env File

Create a file named `.env` in the root directory (same level as CLAUDE.md):

```bash
# For development, use these settings:
cat > .env << 'EOF'
# Environment
ENVIRONMENT=development

# Database (if using Docker Compose: postgres is the hostname)
DATABASE_URL=postgresql://apgi_dev:dev_password@localhost:5432/apgi_api_dev

# Redis (if using Docker Compose: redis is the hostname)
REDIS_URL=redis://localhost:6379/0

# Security keys (use the values you generated above)
JWT_SECRET_KEY=<paste the value from jwt generation above>
CURSOR_SIGNING_KEY=<paste the value from cursor generation above>

# Optional
API_VERSION=v1
DEBUG=true
EOF
```

**Replace** the placeholder values with your generated keys.

## Part 2: Start Services (10-15 minutes)

### Option A: Using Docker Compose (Recommended, Easiest)

```bash
# Start all services (API, PostgreSQL, Redis, Celery worker)
./scripts/start.sh

# Or manually:
docker-compose -f deployment/docker-compose.yml up

# You should see:
# - postgresql starting
# - redis starting
# - api starting (wait for "Application startup complete")
# - celery_worker starting
```

This single command starts everything. Docker will download images (first time only), so wait 2-3 minutes.

**When complete**, you'll see:

```text
apgi-api-api-1  | INFO:     Application startup complete
```

### Option B: Manual Setup (Advanced)

If you prefer to run services manually without Docker:

```bash
# Terminal 1: PostgreSQL and Redis (using Docker)
docker-compose -f deployment/docker-compose.yml up postgres redis

# Terminal 2: Alembic migrations
alembic upgrade head

# Terminal 3: FastAPI development server
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# Terminal 4: Celery worker
celery -A app.celery_app worker --loglevel=info --concurrency=2
```

## Part 3: Verify It's Running (5 minutes)

### Check the API Health

```bash
# In a new terminal (or your browser)
curl http://localhost:8000/health

# You should get:
# {"status":"healthy","timestamp":"2024-03-26T..."}
```

### Open the Interactive API Documentation

Open your browser and navigate to:

```text
http://localhost:8000/docs
```

You should see:

- A Swagger UI with all API endpoints
- Try-it-out buttons for each endpoint
- Request/response examples

### Verify Database and Redis

```bash
# Check PostgreSQL is running
docker ps | grep postgres

# Check Redis is running
docker ps | grep redis

# Check Celery worker is running
docker ps | grep celery
```

## Part 4: Explore the API (30 minutes)

### Step 4a: Create a User Account

In the API docs (`http://localhost:8000/docs`), find the **POST /v1/auth/register** endpoint.

1. Click "Try it out"
2. Fill in the request body:

```json
{
  "username": "student",
  "email": "student@example.com",
  "password": "SecurePassword123!"
}
```

1. Click "Execute"
2. You should get a 201 response with user details

### Step 4b: Login and Get a Token

Find **POST /v1/auth/login** endpoint:

1. Click "Try it out"
2. Fill in:

   ```json
   {
     "username": "student",
     "password": "SecurePassword123!"
   }
   ```

3. Click "Execute"

4. You'll get back an `access_token` — **copy this**

### Step 4c: Authorize the API Docs

At the top of the API docs page, click the green "Authorize" button.

Paste your token:

```http
Bearer <your_access_token>
```

Click "Authorize" and close the dialog. Now all your requests will be authenticated!

### Step 4d: Create an APGI Session

Find **POST /v1/sessions** endpoint:

1. Click "Try it out"
2. Fill in the request body:

```json
{
 "description": "My first APGI simulation",
 "config": {
   "simulation_parameters": {
     "time_steps": 100,
      "initial_precision_gate": 0.3,
      "noise_level": 0.2
    }
  }
}
```

1. Click "Execute"
2. You'll get back a session object with `session_id`, state, etc.

**Copy the session_id** — you'll need it next.

### Step 4e: Check Session Status

Find the **GET /v1/sessions/{session_id}** endpoint:

1. Click "Try it out"
2. Paste your `session_id` from step 4d
3. Click "Execute"
4. You see your session's current configuration and state

### Step 4f: Start the Session

Find the **POST /v1/sessions/{session_id}/start** endpoint:

1. Click "Try it out"
2. Paste your `session_id`
3. Click "Execute"
4. The session state changes from `created` to `running`

### Step 4g: Submit an Experimental Task

Find **POST /v1/sessions/{session_id}/tasks** endpoint:

1. Click "Try it out"
2. Paste your `session_id`
3. Fill in the request body:

   ```json
   {
     "task_type": "apply_stimulus",
     "parameters": {
       "stimulus_type": "visual",
       "expected": true,
       "intensity": 0.7
     }
   }
   ```

4. Click "Execute"
5. You'll get a task ID back

### Step 4h: Check Task Status

Find the **GET /v1/sessions/{session_id}/tasks/{task_id}** endpoint:

1. Click "Try it out"
2. Paste your session_id and task_id
3. Click "Execute"
4. See the task status (should be `pending` → `running` → `completed`)

### Step 4i: Export Session Results

Find **POST /v1/sessions/{session_id}/export** endpoint:

1. Click "Try it out"
2. Paste your session_id
3. Fill in the request body:

   ```json
   {
     "format": "json"
   }
   ```

4. Click "Execute"
5. You get a download URL or the data itself

## Part 5: Advanced Features (Optional, 20 minutes)

### Step 5a: Create an API Key

Find **POST /v1/api-keys** endpoint:

1. Click "Try it out"
2. Fill in the request body:

```json
{
  "name": "My Development Key",
  "description": "Key for local development"
}
```

1. Click "Execute"
2. You'll get back a `key_id`, `key_prefix`, and the full key (shown only once)
3. **Copy the full key** — you'll need it for programmatic access

### Step 5b: List Your API Keys

Find **GET /v1/api-keys** endpoint:

1. Click "Try it out"
2. Click "Execute"
3. You'll see all your API keys with their prefixes and metadata

### Step 5c: Create a Session Template

Find **POST /v1/templates** endpoint:

1. Click "Try it out"
2. Fill in the request body:

```json
{
  "name": "Visual Experiment Template",
  "description": "Standard configuration for visual stimulus experiments",
  "config": {
    "simulation_parameters": {
      "time_steps": 100,
      "initial_precision_gate": 0.3,
      "noise_level": 0.2
    }
  },
  "is_public": false
}
```

1. Click "Execute"
2. You'll get back a `template_id`

### Step 5d: List Available Templates

Find **GET /v1/templates** endpoint:

1. Click "Try it out"
2. Click "Execute"
3. You'll see all public templates and your own templates

### Step 5e: Export Summary Statistics

Find **GET /v1/sessions/{session_id}/export/summary** endpoint:

1. Click "Try it out"
2. Paste your `session_id` from step 4d
3. Click "Execute"
4. You'll get aggregated statistics about your session

### Step 5f: Export Time Series Data

Find **GET /v1/sessions/{session_id}/export/timeseries** endpoint:

1. Click "Try it out"
2. Paste your `session_id`
3. Click "Execute"
4. You'll get time-indexed state data for analysis

### Step 5g: Export Event Analysis

Find **GET /v1/sessions/{session_id}/export/events** endpoint:

1. Click "Try it out"
2. Paste your `session_id`
3. Click "Execute"
4. You'll get ignition events and analysis data

### Step 5h: View Business Metrics Dashboard

Find **GET /v1/metrics/dashboard** endpoint:

1. Click "Try it out"
2. Click "Execute"
3. You'll get comprehensive metrics about:
   - User statistics
   - Session activity
   - Task completion rates
   - Template usage

## Part 6: Explore Locally (Optional)

### View the Database

If you have a SQL client (DBeaver, pgAdmin, etc.):

```bash
# Connection details:
Host: localhost
Port: 5432
Database: apgi_api_dev
Username: apgi_dev
Password: dev_password
```

You can browse:

- `users` table (your account)
- `sessions` table (your session)
- `tasks` table (your task)
- `session_data` table (results)

### View Application Metrics

Open your browser to:

```text
http://localhost:8000/metrics
```

You'll see Prometheus metrics about:

- Number of API requests
- Response times
- Error rates
- Task queue status

### View API Documentation (OpenAPI)

```bash
# Get the full OpenAPI spec
curl http://localhost:8000/openapi.json | python -m json.tool | head -100
```

## Part 7: Understanding What You Just Did

You just:

1. **Set up a development environment** with all services running
2. **Authenticated with JWT** (the token you got is a signed token containing your user info)
3. **Created an APGI session** (a simulation with specific parameters)
4. **Ran a simulation** (state changed from created → running)
5. **Submitted an experimental task** (a stimulus applied to the APGI system)
6. **Monitored progress** (checked task status)
7. **Exported results** (downloaded the simulation data)
8. **Created an API key** for programmatic access
9. **Created a session template** for reusable configurations
10. **Explored advanced export options** (summary, timeseries, events)
11. **Viewed business metrics** for operational insights

This entire flow is what APGI researchers would do to:

- Set up a consciousness modeling experiment
- Apply a stimulus (e.g., visual flash)
- Measure how the system responded (precision gates, synchrony, consciousness level)
- Analyze results

## Troubleshooting

### "Connection refused" on localhost:8000

The API didn't start. Check Docker logs:

```bash
docker-compose -f deployment/docker-compose.yml logs api
```

Look for errors like missing environment variables or database connection failures.

### "unauthorized" or "invalid token" errors

Make sure you:

1. Got a valid token from login (should start with `eyJ`)
2. Clicked "Authorize" in the API docs
3. Token format is exactly: `Bearer <token>` (with space)

### Database errors

The database migration might have failed. Try:

```bash
docker-compose -f deployment/docker-compose.yml exec api alembic upgrade head
```

Or reset everything:

```bash
docker-compose -f deployment/docker-compose.yml down -v
./scripts/start.sh
```

### Celery worker not working

Check if it's running:

```bash
docker ps | grep celery
```

If it's stopped:

```bash
docker-compose -f deployment/docker-compose.yml up celery_worker
```

## What's Running

```text
┌─────────────────────────────────────┐
│    FastAPI Application (Port 8000)  │
│  - Authentication (JWT)             │
│  - Session management               │
│  - Task submission                  │
│  - Metrics and health checks        │
└────────────┬────────────────────────┘
             │
    ┌────────┴────────┐
    ↓                 ↓
┌─────────┐      ┌──────────┐
│PostgreSQL│      │  Redis   │
│Database  │      │Cache/Queue
└────┬─────┘      └────┬─────┘
     │                 │
     └────────┬────────┘
              ↓
     ┌────────────────┐
     │ Celery Worker  │
     │ (Task Queue)   │
     └────────────────┘
```

## Next Steps

🎓 **You now understand APGI and can use the API!**

- **Module 1 Complete**: Theory + Setup + Exploration ✅

👉 **Ready for Module 2?** Start learning how this API was built!

See the main COURSE.md for what's next.

## Key Concepts You've Explored

- **Sessions**: Long-running simulations with configurations
- **Authentication**: JWT tokens for secure API access
- **API Keys**: Programmatic access with secure key management
- **Session Templates**: Reusable configuration presets
- **Tasks**: Asynchronous operations (stimuli, measurements)
- **State Persistence**: Full APGI state saved in database
- **Data Export**: Multiple export formats (JSON, CSV, summary, timeseries, events)
- **Metrics**: Real-time monitoring of system performance and business intelligence

These are all concepts we'll dive deep into in upcoming modules.

---

**Congratulations!** You have a working APGI system. Now let's learn how to build one.
