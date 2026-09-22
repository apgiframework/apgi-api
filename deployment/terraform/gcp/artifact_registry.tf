resource "google_artifact_registry_repository" "apgi_core" {
  project       = var.project_id
  location      = var.region
  repository_id = "apgi-images"
  format        = "DOCKER"
  description   = "apgi-api (this repo's) container images."

  labels = var.labels
}
