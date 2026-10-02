# Uptime checks, error-rate alerting, and the notification channel that pages
# a human. Structured logging is already emitted by the app as JSON with a
# Cloud-Logging-compatible `severity` field (app/middleware/logging.py); Cloud
# Run forwards container stdout/stderr to Cloud Logging automatically, and
# Error Reporting mines entries with severity=ERROR for stack traces from
# there — no separate agent to install.

resource "google_monitoring_notification_channel" "email" {
  project      = var.project_id
  display_name = "APGI on-call (${var.environment})"
  type         = "email"

  labels = {
    email_address = var.alert_notification_email
  }
}

locals {
  # Probe the custom domain when one is mapped, otherwise the service's own
  # *.run.app URL, so the research environment is monitored too and nobody
  # needs to hand-create a console policy for it.
  uptime_host = var.api_domain != null ? var.api_domain : trimsuffix(trimprefix(google_cloud_run_v2_service.apgi_core.uri, "https://"), "/")
}

resource "google_monitoring_uptime_check_config" "health" {
  project      = var.project_id
  display_name = "apgi-api /health/ready (${var.environment})"
  # /health/ready opens DB and Redis connections; with min_instances = 0 the
  # first probe after idle also pays a Cloud Run cold start. 10s was too tight
  # and produced single-region timeouts on a healthy service.
  timeout = "30s"
  period  = "60s"

  http_check {
    path         = "/health/ready"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = local.uptime_host
    }
  }
}

resource "google_monitoring_alert_policy" "uptime_failure" {
  project      = var.project_id
  display_name = "apgi-api uptime check failing (${var.environment})"
  combiner     = "OR"

  # Fires on a real outage, not on one probe region's network blip:
  #   - ALIGN_NEXT_OLDER takes each region's latest result (true/false),
  #   - REDUCE_COUNT_FALSE grouped by host collapses all regions into ONE
  #     series per host, so an outage opens one incident (one email), not one
  #     per checker region,
  #   - threshold > 1 means at least two regions must fail at once,
  #   - duration 300s means they must keep failing for five minutes.
  # The previous per-region "fraction passed < 1" condition alerted whenever
  # a single checker out of nine missed a probe (value 0.888…), then closed
  # and re-opened on every blip.
  conditions {
    display_name = "Uptime check failing in 2+ regions for 5 min"
    condition_threshold {
      filter = join(" AND ", [
        "resource.type=\"uptime_url\"",
        "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\"",
        "metric.label.\"check_id\"=\"${google_monitoring_uptime_check_config.health.uptime_check_id}\"",
      ])
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "300s"
      aggregations {
        alignment_period     = "1200s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.host"]
      }
      trigger {
        count = 1
      }
    }
  }

  alert_strategy {
    auto_close = "1800s"
  }

  documentation {
    mime_type = "text/markdown"
    content   = "apgi-api `/health/ready` on `${local.uptime_host}` has failed from two or more uptime-check regions for five minutes. Check the Cloud Run revision logs and the Cloud SQL / Memorystore status."
  }

  notification_channels = [google_monitoring_notification_channel.email.id]
}

# Both resources used to be gated on `count = var.api_domain != null ? 1 : 0`.
# Keep existing production state instead of destroying and recreating them.
moved {
  from = google_monitoring_uptime_check_config.health[0]
  to   = google_monitoring_uptime_check_config.health
}

moved {
  from = google_monitoring_alert_policy.uptime_failure[0]
  to   = google_monitoring_alert_policy.uptime_failure
}

resource "google_logging_metric" "error_severity_count" {
  project = var.project_id
  name    = "apgi-${var.environment}-error-log-count"
  filter  = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${google_cloud_run_v2_service.apgi_core.name}\" AND severity=ERROR"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

resource "google_monitoring_alert_policy" "error_rate" {
  project      = var.project_id
  display_name = "apgi-api elevated error rate (${var.environment})"
  combiner     = "OR"

  conditions {
    display_name = "ERROR-severity log rate"
    condition_threshold {
      filter          = "resource.type=\"cloud_run_revision\" AND metric.type=\"logging.googleapis.com/user/${google_logging_metric.error_severity_count.name}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 10
      duration        = "300s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_RATE"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]
}
