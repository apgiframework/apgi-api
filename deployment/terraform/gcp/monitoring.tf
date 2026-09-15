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

resource "google_monitoring_uptime_check_config" "health" {
  count        = var.api_domain != null ? 1 : 0
  project      = var.project_id
  display_name = "apgi-core /health/ready (${var.environment})"
  timeout      = "10s"
  period       = "60s"

  http_check {
    path         = "/health/ready"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      host = var.api_domain
    }
  }
}

resource "google_monitoring_alert_policy" "uptime_failure" {
  count        = var.api_domain != null ? 1 : 0
  project      = var.project_id
  display_name = "apgi-core uptime check failing (${var.environment})"
  combiner     = "OR"

  conditions {
    display_name = "Uptime check failure"
    condition_threshold {
      filter = join(" AND ", [
        "resource.type=\"uptime_url\"",
        "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\"",
        "metric.label.\"check_id\"=\"${google_monitoring_uptime_check_config.health[0].uptime_check_id}\"",
      ])
      comparison      = "COMPARISON_LT"
      threshold_value = 1
      duration        = "180s"
      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_FRACTION_TRUE"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]
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
  display_name = "apgi-core elevated error rate (${var.environment})"
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
