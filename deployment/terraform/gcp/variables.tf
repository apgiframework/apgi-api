variable "environment" {
  description = "Deployment environment: \"production\" or \"research\" (identifiers.yaml: infrastructure.projects)."
  type        = string
  validation {
    condition     = contains(["production", "research"], var.environment)
    error_message = "environment must be \"production\" or \"research\"."
  }
}

variable "project_id" {
  description = "GCP project ID for this environment. Fill in identifiers.yaml infrastructure.projects.<environment> once created."
  type        = string
}

variable "region" {
  description = "Primary GCP region."
  type        = string
  default     = "us-central1"
}

variable "github_repository" {
  description = "owner/repo allowed to assume the CI deployer service account via Workload Identity Federation."
  type        = string
  default     = "apgiframework/apgi-api"
}

variable "github_deploy_ref" {
  description = "Git ref (e.g. refs/heads/main) that WIF trusts for deploys to this environment. Use refs/tags/v* for production if you only want tagged releases to deploy."
  type        = string
  default     = "refs/heads/main"
}

variable "api_domain" {
  description = "Custom domain to map to Cloud Run (production only: api.apgiframework.com). Leave null for research."
  type        = string
  default     = null
}

variable "container_image" {
  description = "Fully-qualified image reference deployed on `terraform apply`. CI overwrites this on every deploy via `gcloud run deploy --image`; this value only matters for the first bootstrap apply."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello" # placeholder until the first CI build pushes a real image
}

variable "min_instances" {
  description = "Cloud Run minimum instance count."
  type        = number
  default     = 0
}

variable "max_instances" {
  description = "Cloud Run maximum instance count."
  type        = number
  default     = 10
}

variable "db_tier" {
  description = "Cloud SQL machine tier."
  type        = string
  default     = "db-custom-1-3840"
}

variable "db_high_availability" {
  description = "Whether Cloud SQL runs REGIONAL (HA) vs ZONAL availability."
  type        = bool
  default     = false
}

variable "redis_tier" {
  description = "Memorystore Redis service tier: BASIC or STANDARD_HA."
  type        = string
  default     = "BASIC"
}

variable "redis_memory_size_gb" {
  description = "Memorystore Redis instance size in GB."
  type        = number
  default     = 1
}

variable "alert_notification_email" {
  description = "Email address for uptime/error-rate alert notifications."
  type        = string
}

variable "backup_retention_days" {
  description = "Number of daily Cloud SQL backups to retain (§ Backup configuration and restore drill)."
  type        = number
  default     = 30
}

variable "secret_rotation_period_seconds" {
  description = "Reminder-notification cadence for Secret Manager rotation (does not rotate values itself — see docs/DEPLOYMENT.md Secrets Rotation SOP)."
  type        = string
  default     = "7776000s" # 90 days
}

variable "labels" {
  description = "Common resource labels."
  type        = map(string)
  default = {
    system = "apgi"
  }
}
