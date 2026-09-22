# IAM matrix as code. Every service-account/role grant for this environment is
# enumerated here — no console click-ops, no broad Editor/Owner grants.
#
# Reviewing a change to access always means reviewing a diff of this file.

resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = "apgi-api-runtime"
  display_name = "apgi-api Cloud Run runtime (${var.environment})"
  description  = "Identity the apgi-api Cloud Run service runs as. Least-privilege — no IAM admin, no project-level roles."
}

locals {
  # role => list of member service accounts (by resource reference) that need it.
  # Keep every grant project-scoped and named for the resource it protects;
  # avoid roles/editor and roles/owner entirely.
  iam_matrix = {
    # CI deployer: build images, push to Artifact Registry, deploy Cloud Run
    # revisions, and hand the runtime SA to the new revision (actAs).
    "roles/run.developer"             = [google_service_account.ci_deployer.member]
    "roles/artifactregistry.writer"   = [google_service_account.ci_deployer.member]
    "roles/iam.serviceAccountUser"    = [google_service_account.ci_deployer.member]
    "roles/cloudsql.client"           = [google_service_account.runtime.member]
    "roles/logging.logWriter"         = [google_service_account.ci_deployer.member, google_service_account.runtime.member]
    "roles/monitoring.metricWriter"   = [google_service_account.runtime.member]
    "roles/cloudtrace.agent"          = [google_service_account.runtime.member]
    "roles/errorreporting.writer"     = [google_service_account.runtime.member]
    # Runtime needs to read secret VALUES (bound per-secret too, see secrets.tf)
    # and write de-identified rows to BigQuery / raw trial events to GCS
    # (bucket- and dataset-scoped bindings live in cloudsql.tf / bigquery.tf,
    # not here, so access to participant data is auditable per-resource).
  }

  # Flatten {role: [members]} into one binding per (role, member) pair.
  iam_bindings = merge([
    for role, members in local.iam_matrix : {
      for member in members : "${role}=>${member}" => {
        role   = role
        member = member
      }
    }
  ]...)
}

resource "google_project_iam_member" "matrix" {
  for_each = local.iam_bindings
  project  = var.project_id
  role     = each.value.role
  member   = each.value.member
}
