# apgi-core as a private-by-default Cloud Run v2 service. The service itself
# allows unauthenticated ingress (it's a public REST API — auth is enforced at
# the application layer by JWT/API-key middleware, not by IAM), but every
# resource it talks to (Cloud SQL, Redis, Secret Manager, GCS, BigQuery) is
# reachable only via its dedicated runtime service account.

locals {
  base_url = var.api_domain != null ? "https://${var.api_domain}" : "https://apgi-core-${var.environment}.run.app"

  secret_env_names = [
    "JWT_SECRET_KEY",
    "CURSOR_SIGNING_KEY",
    "WEBHOOK_SECRET_KEY",
    "DATABASE_URL",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "PII_ENCRYPTION_KEY",
  ]
}

resource "google_cloud_run_v2_service" "apgi_core" {
  project  = var.project_id
  name     = "apgi-core"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.runtime.email

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    vpc_access {
      connector = google_vpc_access_connector.apgi.id
      egress    = "PRIVATE_RANGES_ONLY" # Cloud SQL/Redis via the connector; Secret Manager/Stripe/etc over the public Cloud Run egress path.
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.apgi.connection_name]
      }
    }

    containers {
      image = var.container_image

      ports {
        container_port = 8000
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }

      startup_probe {
        http_get {
          path = "/health/ready"
        }
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 6
      }

      liveness_probe {
        http_get {
          path = "/health/live"
        }
        period_seconds = 30
      }

      env {
        name  = "ENVIRONMENT"
        value = var.environment == "production" ? "production" : "staging"
      }
      env {
        name  = "BASE_URL"
        value = local.base_url
      }
      env {
        name  = "REDIS_URL"
        value = "redis://:${google_redis_instance.apgi.auth_string}@${google_redis_instance.apgi.host}:${google_redis_instance.apgi.port}/0"
      }
      env {
        name  = "CELERY_BROKER_URL"
        value = "redis://:${google_redis_instance.apgi.auth_string}@${google_redis_instance.apgi.host}:${google_redis_instance.apgi.port}/1"
      }
      env {
        name  = "CELERY_RESULT_BACKEND"
        value = "redis://:${google_redis_instance.apgi.auth_string}@${google_redis_instance.apgi.host}:${google_redis_instance.apgi.port}/2"
      }
      env {
        name  = "CORS_ORIGINS"
        value = "https://apgiframework.com"
      }
      env {
        name  = "TRIAL_EVENTS_BUCKET"
        value = google_storage_bucket.raw_trial_events.name
      }
      env {
        name  = "BIGQUERY_DEIDENTIFIED_DATASET"
        value = google_bigquery_dataset.deidentified_export.dataset_id
      }
      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }

      dynamic "env" {
        for_each = toset(local.secret_env_names)
        content {
          name = env.value
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.app[env.value].secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  depends_on = [
    google_project_iam_member.matrix,
    google_secret_manager_secret_iam_member.runtime_accessor,
  ]

  lifecycle {
    # CI deploys new revisions with `gcloud run deploy --image=...`, which
    # Terraform must not fight on the next apply.
    ignore_changes = [template[0].containers[0].image]
  }
}

# Public read access to the service itself; every request is still subject to
# the application's own JWT/API-key auth and rate limiting.
resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.apgi_core.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_domain_mapping" "api" {
  count    = var.api_domain != null ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = var.api_domain

  metadata {
    namespace = var.project_id
  }

  spec {
    route_name = google_cloud_run_v2_service.apgi_core.name
  }
}
