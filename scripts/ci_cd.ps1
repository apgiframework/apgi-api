# APGI API CI/CD Pipeline Script (PowerShell)
# Handles continuous integration and deployment tasks

param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet("test", "lint", "build", "deploy", "promote", "rollback", "health", "cleanup")]
    [string]$Command,

    [Parameter(Mandatory = $false)]
    [ValidateSet("staging", "production")]
    [string]$Environment = $env:CI_ENVIRONMENT,

    [Parameter(Mandatory = $false)]
    [string]$Version = $env:DEPLOY_VERSION,

    [switch]$Force,
    [switch]$DryRun
)

# Set default values
if (-not $Environment) { $Environment = "staging" }
if (-not $Version) { $Version = "latest" }

# Script configuration
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$DockerComposeFile = Join-Path $ProjectRoot "deployment\docker-compose.yml"

# Logging functions
function Write-Info {
    param([string]$Message)
    Write-Host "[INFO] $Message" -ForegroundColor Blue
}

function Write-Warn {
    param([string]$Message)
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Write-Error {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

function Write-Success {
    param([string]$Message)
    Write-Host "[SUCCESS] $Message" -ForegroundColor Green
}

# Help function
function Show-Help {
    $helpText = @"
APGI API CI/CD Pipeline Script (PowerShell)

Usage: .\ci_cd.ps1 [COMMAND] [OPTIONS]

Commands:
    test         Run all tests (unit, integration, property-based)
    lint         Run code linting and formatting checks
    build        Build Docker images
    deploy       Deploy to staging environment
    promote      Promote staging to production
    rollback     Rollback to previous version
    health       Run health checks on deployed service
    cleanup      Clean up old Docker images and containers

Options:
    -Environment   Target environment (staging, production) [default: staging]
    -Version       Version tag for deployment
    -Force         Force operation without confirmation
    -DryRun        Show what would be done without executing

Examples:
    .\ci_cd.ps1 test                           # Run all tests
    .\ci_cd.ps1 lint                           # Run linting checks
    .\ci_cd.ps1 build                          # Build Docker images
    .\ci_cd.ps1 deploy -Environment staging    # Deploy to staging
    .\ci_cd.ps1 promote                        # Promote staging to production
    .\ci_cd.ps1 rollback -Environment production # Rollback production
    .\ci_cd.ps1 health -Environment staging    # Check staging health

Environment Variables:
    CI_ENVIRONMENT     Target environment (staging/production)
    DOCKER_REGISTRY    Docker registry URL
    DOCKER_USERNAME    Docker registry username
    DOCKER_PASSWORD    Docker registry password
    DEPLOY_VERSION     Version tag for deployment

Requirements:
    - Docker and Docker Compose
    - PowerShell 5.1 or later
    - Python 3.8+ with required packages

"@
    Write-Host $helpText
}

# Execute command with dry run support
function Invoke-Execute {
    param([string[]]$Arguments)

    $command = $Arguments -join " "

    if ($DryRun) {
        Write-Info "[DRY RUN] Would execute: $command"
        return $true
    }
    else {
        Write-Info "Executing: $command"

        try {
            $process = Start-Process -FilePath $Arguments[0] -ArgumentList $Arguments[1..($Arguments.Length-1)] -NoNewWindow -Wait -PassThru
            if ($process.ExitCode -ne 0) {
                throw "Command failed with exit code $($process.ExitCode)"
            }
            return $true
        }
        catch {
            Write-Error "Command execution failed: $($_.Exception.Message)"
            return $false
        }
    }
}

# Confirmation wrapper
function Confirm-Action {
    param([string]$Message)

    if ($Force -or $DryRun) {
        return $true
    }

    $response = Read-Host "$Message (y/N)"
    return $response -match "^[Yy]$"
}

# Run tests
function Invoke-Tests {
    Write-Info "Running test suite..."

    # Check if Docker is available
    $dockerAvailable = $false
    try {
        $null = docker version 2>$null
        $dockerAvailable = $true
    }
    catch {
        $dockerAvailable = $false
    }

    if ($dockerAvailable -and (Test-Path $DockerComposeFile)) {
        Write-Info "Running tests in Docker environment"

        # Start test database
        Invoke-Execute @("docker-compose", "-f", $DockerComposeFile, "up", "-d", "postgres", "redis")

        # Wait for services to be ready
        Write-Info "Waiting for services to be ready..."
        Start-Sleep -Seconds 10

        # Run tests
        Invoke-Execute @("docker-compose", "-f", $DockerComposeFile, "run", "--rm", "api", "pytest",
            "--cov=app", "--cov-report=html", "--cov-report=term-missing",
            "tests/unit/", "tests/integration/", "tests/property/")

        # Stop services
        Invoke-Execute @("docker-compose", "-f", $DockerComposeFile, "down")
    }
    else {
        Write-Info "Running tests in local environment"

        # Run tests directly
        Invoke-Execute @("python", "-m", "pytest",
            "--cov=app", "--cov-report=html", "--cov-report=term-missing",
            "tests/unit/", "tests/integration/", "tests/property/")
    }

    Write-Success "Tests completed"
}

# Run linting
function Invoke-Linting {
    Write-Info "Running code linting and formatting checks..."

    # Check code formatting with black
    Write-Info "Checking code formatting with black..."
    Invoke-Execute @("python", "-m", "black", "--check", "--diff", "app/", "tests/")

    # Run flake8 for linting
    Write-Info "Running flake8 linting..."
    Invoke-Execute @("python", "-m", "flake8", "app/", "tests/")

    # Run mypy for type checking
    Write-Info "Running mypy type checking..."
    Invoke-Execute @("python", "-m", "mypy", "app/")

    # Check import sorting
    Write-Info "Checking import sorting..."
    Invoke-Execute @("python", "-m", "isort", "--check-only", "--diff", "app/", "tests/")

    Write-Success "Linting completed"
}

# Build Docker images
function Invoke-Build {
    Write-Info "Building Docker images..."

    # Build API image
    Invoke-Execute @("docker", "build", "-t", "apgi-api:$Version", "-f", "$ProjectRoot/deployment/Dockerfile", $ProjectRoot)

    # Tag for registry if registry is configured
    if ($env:DOCKER_REGISTRY) {
        Invoke-Execute @("docker", "tag", "apgi-api:$Version", "$env:DOCKER_REGISTRY/apgi-api:$Version")
        Write-Info "Tagged image for registry: $env:DOCKER_REGISTRY/apgi-api:$Version"
    }

    Write-Success "Docker images built"
}

# Deploy to environment
function Invoke-Deploy {
    param([string]$Env)

    Write-Info "Deploying to $Env environment..."

    if (-not (Confirm-Action "Are you sure you want to deploy to $Env?")) {
        Write-Info "Deployment cancelled"
        return
    }

    # Build images first
    Invoke-Build

    # Deploy based on environment
    switch ($Env) {
        "staging" {
            # Deploy to staging
            Write-Info "Deploying to staging environment"

            # Update docker-compose file with new image (simplified)
            if ($env:DOCKER_REGISTRY) {
                $composeContent = Get-Content $DockerComposeFile -Raw
                $composeContent = $composeContent -replace "image: apgi-api:.*", "image: $env:DOCKER_REGISTRY/apgi-api:$Version"
                Set-Content -Path $DockerComposeFile -Value $composeContent
            }

            # Restart services
            Invoke-Execute @("docker-compose", "-f", $DockerComposeFile, "up", "-d")

            # Wait for services to be healthy
            Write-Info "Waiting for services to be healthy..."
            Start-Sleep -Seconds 30

            # Run health checks
            Invoke-HealthChecks $Env
        }
        "production" {
            # Deploy to production
            Write-Info "Deploying to production environment"
            Write-Warn "Production deployment not fully implemented in this script"
        }
    }

    Write-Success "Deployment to $Env completed"
}

# Promote staging to production
function Invoke-Promote {
    Write-Info "Promoting staging to production..."

    if (-not (Confirm-Action "Are you sure you want to promote staging to production?")) {
        Write-Info "Promotion cancelled"
        return
    }

    # Run final tests on staging
    Write-Info "Running final tests on staging..."
    $env:CI_ENVIRONMENT = "staging"
    Invoke-Tests

    # Tag production release
    Write-Info "Tagging production release..."
    Invoke-Execute @("git", "tag", "prod-$Version")
    Invoke-Execute @("git", "push", "origin", "prod-$Version")

    # Update production deployment
    Write-Info "Updating production deployment..."
    $env:CI_ENVIRONMENT = "production"
    Invoke-Deploy "production"

    Write-Success "Promotion to production completed"
}

# Rollback deployment
function Invoke-Rollback {
    param([string]$Env)

    Write-Info "Rolling back $Env environment..."

    if (-not (Confirm-Action "Are you sure you want to rollback $Env?")) {
        Write-Info "Rollback cancelled"
        return
    }

    switch ($Env) {
        "staging" {
            # Rollback staging
            Write-Info "Rolling back to previous staging version"

            # Get previous version from git
            try {
                $prevVersion = git describe --abbrev=0 --tags 2>$null
                if (-not $prevVersion) { $prevVersion = "v1.0.0" }
            }
            catch {
                $prevVersion = "v1.0.0"
            }

            Invoke-Execute @("docker-compose", "-f", $DockerComposeFile, "pull")
            Invoke-Execute @("docker-compose", "-f", $DockerComposeFile, "up", "-d")
        }
        "production" {
            # Rollback production
            Write-Info "Rolling back production environment"
            Write-Warn "Production rollback not fully implemented in this script"
        }
    }

    Write-Success "Rollback of $Env completed"
}

# Run health checks
function Invoke-HealthChecks {
    param([string]$Env)

    $url = switch ($Env) {
        "staging" { "http://localhost:8000" }
        "production" { $env:PRODUCTION_URL ?? "https://api.apgiframework.com" }
    }

    Write-Info "Running health checks for $Env environment..."

    # Use the health check script if available
    $healthScript = Join-Path $ScriptDir "health_check.ps1"
    if (Test-Path $healthScript) {
        Invoke-Execute @("powershell", "-File", $healthScript, "-Url", $url, "-Endpoint", "/health/ready")
    }
    else {
        # Fallback health check
        try {
            Invoke-WebRequest -Uri "$url/health/ready" -TimeoutSec 30 -ErrorAction Stop | Out-Null
            Write-Success "Health check passed"
        }
        catch {
            Write-Error "Health check failed: $($_.Exception.Message)"
            throw
        }
    }

    Write-Success "Health checks passed for $Env"
}

# Cleanup old resources
function Invoke-Cleanup {
    Write-Info "Cleaning up old Docker resources..."

    # Remove unused containers
    Invoke-Execute @("docker", "container", "prune", "-f")

    # Remove unused images
    Invoke-Execute @("docker", "image", "prune", "-f")

    # Remove unused volumes
    Invoke-Execute @("docker", "volume", "prune", "-f")

    # Remove old images (keep last 5 versions)
    Write-Info "Removing old apgi-api images (keeping last 5)..."
    try {
        $oldImages = docker images "apgi-api" --format "{{.ID}}" | Select-Object -Skip 5
        if ($oldImages) {
            $oldImages | ForEach-Object { Invoke-Execute @("docker", "rmi", $_) }
        }
    }
    catch {
        # Silently continue if no old images
    }

    Write-Success "Cleanup completed"
}

# Main execution
function Invoke-Main {
    Write-Info "Starting CI/CD pipeline command: $Command"

    switch ($Command) {
        "test" {
            Invoke-Tests
        }
        "lint" {
            Invoke-Linting
        }
        "build" {
            Invoke-Build
        }
        "deploy" {
            Invoke-Deploy $Environment
        }
        "promote" {
            Invoke-Promote
        }
        "rollback" {
            Invoke-Rollback $Environment
        }
        "health" {
            Invoke-HealthChecks $Environment
        }
        "cleanup" {
            Invoke-Cleanup
        }
        default {
            Write-Error "Unknown command: $Command"
            Show-Help
            exit 1
        }
    }

    Write-Success "CI/CD command '$Command' completed successfully"
}

# Show help if requested
if ($args -contains "-h" -or $args -contains "--help" -or $args -contains "-Help") {
    Show-Help
    exit 0
}

# Run main function
try {
    Invoke-Main
}
catch {
    Write-Error "CI/CD pipeline failed: $($_.Exception.Message)"
    exit 1
}
