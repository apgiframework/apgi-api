#!/bin/bash

# APGI API CI/CD Pipeline Script
# Handles continuous integration and deployment tasks

set -e  # Exit on any error

# Script configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DOCKER_COMPOSE_FILE="$PROJECT_ROOT/deployment/docker-compose.yml"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

# Help function
show_help() {
    cat << EOF
APGI API CI/CD Pipeline Script

Usage: $0 [COMMAND] [OPTIONS]

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
    -e, --env ENV      Target environment (staging, production) [default: staging]
    -v, --version VER  Version tag for deployment
    -f, --force        Force operation without confirmation
    -d, --dry-run      Show what would be done without executing
    -h, --help         Show this help message

Examples:
    $0 test                    # Run all tests
    $0 lint                    # Run linting checks
    $0 build                   # Build Docker images
    $0 deploy --env staging    # Deploy to staging
    $0 promote                 # Promote staging to production
    $0 rollback --env production # Rollback production
    $0 health --env staging    # Check staging health

Environment Variables:
    CI_ENVIRONMENT     Target environment (staging/production)
    DOCKER_REGISTRY    Docker registry URL
    DOCKER_USERNAME    Docker registry username
    DOCKER_PASSWORD    Docker registry password
    DEPLOY_VERSION     Version tag for deployment

Requirements:
    - Docker and Docker Compose
    - curl
    - Python 3.8+
    - Required Python packages (pytest, black, flake8, etc.)

EOF
}

# Default values
ENVIRONMENT="${CI_ENVIRONMENT:-staging}"
VERSION="${DEPLOY_VERSION:-latest}"
FORCE=false
DRY_RUN=false

# Parse command line arguments
COMMAND=""
while [[ $# -gt 0 ]]; do
    case $1 in
        -e|--env)
            ENVIRONMENT="$2"
            shift 2
            ;;
        -v|--version)
            VERSION="$2"
            shift 2
            ;;
        -f|--force)
            FORCE=true
            shift
            ;;
        -d|--dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        test|lint|build|deploy|promote|rollback|health|cleanup)
            COMMAND="$1"
            shift
            ;;
        *)
            log_error "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

if [[ -z "$COMMAND" ]]; then
    log_error "No command specified"
    show_help
    exit 1
fi

# Validate environment
case $ENVIRONMENT in
    staging|production)
        ;;
    *)
        log_error "Invalid environment: $ENVIRONMENT. Must be staging or production."
        exit 1
        ;;
esac

# Dry run wrapper
execute() {
    if [[ "$DRY_RUN" == "true" ]]; then
        log_info "[DRY RUN] Would execute: $@"
    else
        log_info "Executing: $@"
        "$@"
    fi
}

# Confirmation wrapper
confirm() {
    local message="$1"
    if [[ "$FORCE" == "true" ]] || [[ "$DRY_RUN" == "true" ]]; then
        return 0
    fi

    read -p "$message (y/N): " -n 1 -r
    echo
    [[ $REPLY =~ ^[Yy]$ ]]
}

# Run tests
run_tests() {
    log_info "Running test suite..."

    # Check if we're in Docker or local environment
    if [[ -f "$DOCKER_COMPOSE_FILE" ]] && command -v docker &> /dev/null && docker info &> /dev/null; then
        log_info "Running tests in Docker environment"

        # Start test database
        execute docker-compose -f "$DOCKER_COMPOSE_FILE" up -d postgres redis

        # Wait for services to be ready
        log_info "Waiting for services to be ready..."
        execute sleep 10

        # Run tests
        execute docker-compose -f "$DOCKER_COMPOSE_FILE" run --rm api pytest \
            --cov=app --cov-report=html --cov-report=term-missing \
            tests/unit/ tests/integration/ tests/property/

        # Stop services
        execute docker-compose -f "$DOCKER_COMPOSE_FILE" down
    else
        log_info "Running tests in local environment"

        # Run tests directly
        execute python -m pytest \
            --cov=app --cov-report=html --cov-report=term-missing \
            tests/unit/ tests/integration/ tests/property/
    fi

    log_success "Tests completed"
}

# Run linting
run_linting() {
    log_info "Running code linting and formatting checks..."

    # Check code formatting with black
    log_info "Checking code formatting with black..."
    execute python -m black --check --diff app/ tests/

    # Run Ruff for fast linting (replaces flake8)
    log_info "Running Ruff linting..."
    execute python -m ruff check app/ tests/

    # Run mypy with strict mode for type checking
    log_info "Running mypy type checking with strict mode..."
    execute python -m mypy --strict app/ --ignore-missing-imports --explicit-package-bases

    # Check import sorting with Ruff (replaces isort)
    log_info "Checking import sorting with Ruff..."
    execute python -m ruff check --select I app/ tests/

    log_success "Linting completed"
}

# Build Docker images
build_images() {
    log_info "Building Docker images..."

    # Build API image
    execute docker build -t "apgi-api:$VERSION" -f "$PROJECT_ROOT/deployment/Dockerfile" "$PROJECT_ROOT"

    # Tag for registry if registry is configured
    if [[ -n "$DOCKER_REGISTRY" ]]; then
        execute docker tag "apgi-api:$VERSION" "$DOCKER_REGISTRY/apgi-api:$VERSION"
        log_info "Tagged image for registry: $DOCKER_REGISTRY/apgi-api:$VERSION"
    fi

    log_success "Docker images built"
}

# Deploy to environment
deploy_to_environment() {
    local env="$1"
    log_info "Deploying to $env environment..."

    if ! confirm "Are you sure you want to deploy to $env?"; then
        log_info "Deployment cancelled"
        exit 0
    fi

    # Build images first
    build_images

    # Deploy based on environment
    case $env in
        staging)
            # Deploy to staging (could be Docker Compose, Kubernetes, etc.)
            log_info "Deploying to staging environment"

            # Update docker-compose file with new image
            if [[ -n "$DOCKER_REGISTRY" ]]; then
                execute sed -i "s|image: apgi-api:.*|image: $DOCKER_REGISTRY/apgi-api:$VERSION|g" "$DOCKER_COMPOSE_FILE"
            fi

            # Restart services
            execute docker-compose -f "$DOCKER_COMPOSE_FILE" up -d

            # Wait for services to be healthy
            log_info "Waiting for services to be healthy..."
            execute sleep 30

            # Run health checks
            run_health_checks "$env"
            ;;
        production)
            # Deploy to production
            log_info "Deploying to production environment"

            # More sophisticated production deployment would go here
            # This could involve blue-green deployment, canary releases, etc.
            log_warn "Production deployment not fully implemented in this script"
            ;;
    esac

    log_success "Deployment to $env completed"
}

# Promote staging to production
promote_to_production() {
    log_info "Promoting staging to production..."

    if ! confirm "Are you sure you want to promote staging to production?"; then
        log_info "Promotion cancelled"
        exit 0
    fi

    # In a real scenario, this would involve:
    # 1. Run comprehensive tests on staging
    # 2. Create production release
    # 3. Update production deployment
    # 4. Run smoke tests on production
    # 5. Gradually shift traffic (if using load balancer)

    log_info "Running final tests on staging..."
    ENVIRONMENT="staging" run_tests

    log_info "Tagging production release..."
    # Tag the current staging version for production
    execute git tag "prod-$VERSION"
    execute git push origin "prod-$VERSION"

    log_info "Updating production deployment..."
    # This would trigger a production deployment pipeline
    ENVIRONMENT="production" deploy_to_environment "production"

    log_success "Promotion to production completed"
}

# Rollback deployment
rollback_deployment() {
    local env="$1"
    log_info "Rolling back $env environment..."

    if ! confirm "Are you sure you want to rollback $env?"; then
        log_info "Rollback cancelled"
        exit 0
    fi

    case $env in
        staging)
            # Rollback staging
            log_info "Rolling back to previous staging version"

            # Get previous version from git
            PREV_VERSION=$(git describe --abbrev=0 --tags 2>/dev/null || echo "v1.0.0")
            execute docker-compose -f "$DOCKER_COMPOSE_FILE" pull
            execute docker-compose -f "$DOCKER_COMPOSE_FILE" up -d
            ;;
        production)
            # Rollback production (more complex)
            log_info "Rolling back production environment"

            # This would involve switching back to previous deployment
            # Could use previous Docker image tag, Kubernetes rollout, etc.
            log_warn "Production rollback not fully implemented in this script"
            ;;
    esac

    log_success "Rollback of $env completed"
}

# Run health checks
run_health_checks() {
    local env="$1"
    local url=""

    case $env in
        staging)
            url="http://localhost:8000"
            ;;
        production)
            url="${PRODUCTION_URL:-https://api.apgiframework.com}"
            ;;
    esac

    log_info "Running health checks for $env environment..."

    # Use the health check script
    if [[ -f "$SCRIPT_DIR/health_check.sh" ]]; then
        execute "$SCRIPT_DIR/health_check.sh" --url "$url" --endpoint "/health/ready"
    else
        # Fallback health check
        if command -v curl &> /dev/null; then
            execute curl -f --max-time 30 "$url/health/ready"
        fi
    fi

    log_success "Health checks passed for $env"
}

# Cleanup old resources
cleanup_resources() {
    log_info "Cleaning up old Docker resources..."

    # Remove unused containers
    execute docker container prune -f

    # Remove unused images
    execute docker image prune -f

    # Remove unused volumes
    execute docker volume prune -f

    # Remove old images (keep last 5 versions)
    log_info "Removing old apgi-api images (keeping last 5)..."
    execute docker images "apgi-api" --format "table {{.Repository}}\t{{.Tag}}\t{{.ID}}" | \
        tail -n +2 | sort -r | tail -n +6 | awk '{print $3}' | \
        xargs -r docker rmi 2>/dev/null || true

    log_success "Cleanup completed"
}

# Main execution
main() {
    log_info "Starting CI/CD pipeline command: $COMMAND"

    case $COMMAND in
        test)
            run_tests
            ;;
        lint)
            run_linting
            ;;
        build)
            build_images
            ;;
        deploy)
            deploy_to_environment "$ENVIRONMENT"
            ;;
        promote)
            promote_to_production
            ;;
        rollback)
            rollback_deployment "$ENVIRONMENT"
            ;;
        health)
            run_health_checks "$ENVIRONMENT"
            ;;
        cleanup)
            cleanup_resources
            ;;
        *)
            log_error "Unknown command: $COMMAND"
            show_help
            exit 1
            ;;
    esac

    log_success "CI/CD command '$COMMAND' completed successfully"
}

# Run main function
main "$@"
