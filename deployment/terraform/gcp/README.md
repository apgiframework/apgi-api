# GCP infrastructure (Phase 0C)

Terraform for the two GCP projects named in `identifiers.yaml`
(`infrastructure.projects.production` / `.research`): Cloud Run, Cloud SQL,
Memorystore, Secret Manager, Artifact Registry, Workload Identity Federation,
IAM, and monitoring.

This supersedes `deployment/terraform/main.tf` (AWS), which predates the
decision to run on Google Cloud and is kept only as historical reference —
see the note at the top of that file.

## What Terraform does *not* do

A few steps require a human with organisation-level access and cannot be
expressed as `terraform apply` against a project that doesn't exist yet:

1. **Create the GCP projects** and link a billing account. Once created,
   record the project IDs in `identifiers.yaml` under
   `infrastructure.projects` (replacing the `"TODO"` placeholders) and in
   `environments/{production,research}.tfvars` (copied from the
   `.tfvars.example` files, which are gitignored).
2. **Buy/verify the domain** `apgiframework.com` and delegate DNS so the
   `google_cloud_run_domain_mapping` resource's CNAME/A records can be
   created — Terraform creates the mapping but Cloud Run only marks it
   `Ready` once the DNS records it asks for are in place.
3. **Seed Secret Manager secret values** (`gcloud secrets versions add
   JWT_SECRET_KEY --data-file=-`, etc., for every entry in
   `secrets.tf`'s `local.managed_secrets` except `DATABASE_URL`, which
   Terraform populates itself from the generated Cloud SQL password).
   Terraform manages the secret *containers* and access, never the values.
4. **First `terraform apply` per environment** — run manually from a
   trusted machine with `gcloud auth application-default login`, before any
   CI identity exists to do it. After that, routine deploys go through
   `.github/workflows/deploy.yml` (image build + `gcloud run deploy`), and
   infrastructure changes go through a normal `terraform plan`/`apply` PR
   workflow (not yet automated — plan-on-PR / apply-on-merge is a natural
   Phase 0C follow-up once the projects exist to test it against).

## Usage

```bash
cd deployment/terraform/gcp
terraform init
cp environments/production.tfvars.example environments/production.tfvars  # fill in TODOs
terraform plan  -var-file=environments/production.tfvars
terraform apply -var-file=environments/production.tfvars

cp environments/research.tfvars.example environments/research.tfvars      # fill in TODOs
terraform plan  -var-file=environments/research.tfvars
terraform apply -var-file=environments/research.tfvars
```

Each environment is a separate GCP project and should eventually use a
separate remote state prefix (see the commented `backend "gcs"` block in
`versions.tf`) — never share state between production and research.

## Workload Identity Federation

`wif.tf` creates a pool + OIDC provider trusting only
`github_repository`/`github_deploy_ref` (a ref *prefix* match — e.g.
`refs/tags/v` restricts production to tagged releases). No service-account
key file is ever created or stored as a GitHub secret. Feed
`outputs.wif_provider_resource_name` and
`outputs.ci_deployer_service_account_email` into GitHub Actions *variables*
(not secrets), set per environment (`research`, `production`). The deploy
workflow (`.github/workflows/deploy.yml`) reads:

| Variable | Source |
| --- | --- |
| `GCP_PROJECT_ID_RESEARCH` / `GCP_PROJECT_ID_PRODUCTION` | the environment's GCP project ID |
| `GCP_REGION` | the Cloud Run / Artifact Registry region |
| `GCP_WIF_PROVIDER_RESEARCH` / `GCP_WIF_PROVIDER_PRODUCTION` | `wif_provider_resource_name` |
| `GCP_CI_DEPLOYER_SA_RESEARCH` / `GCP_CI_DEPLOYER_SA_PRODUCTION` | `ci_deployer_service_account_email` |

These are not secrets in the credential sense (they name a principal, not a
password); keeping them as Actions variables avoids hardcoding
project-specific values into the workflow file. If any is missing, the deploy
fails immediately in the "Verify deploy inputs are configured" step.

## IAM matrix

`iam.tf`'s `local.iam_matrix` is the complete list of project-level
role grants. Anything not listed there is not granted — no
`roles/editor`, no `roles/owner`, ever. Reviewing IAM changes means
reviewing a diff of that file (and the per-resource bindings in
`secrets.tf` / `storage.tf` for secret- and bucket-scoped access).

## Secrets rotation schedule

`secrets.tf` sets a 90-day rotation reminder (Pub/Sub notification, not an
automatic value rotation — Secret Manager has no built-in rotator for
application-generated keys like `JWT_SECRET_KEY`). The rotation procedure
itself is documented in `docs/DEPLOYMENT.md` under "Secrets Rotation SOP".
`DATABASE_URL`'s password is Terraform-generated (`random_password`) and
rotates by re-running `terraform apply -replace=random_password.db_app_user`.

## Backup configuration and restore drill

Cloud SQL point-in-time recovery is enabled with daily backups retained per
`var.backup_retention_days` (30 days in production). To run a restore drill:

```bash
# Clone the instance from a backup into a throwaway instance, verify the
# clone boots and the schema/row counts look right, then delete the clone.
gcloud sql backups list --instance=apgi-production
gcloud sql instances clone apgi-production apgi-restore-drill-$(date +%Y%m%d) \
  --point-in-time="<RFC3339 timestamp from the backup you're testing>"
# ... verify against apgi-restore-drill-YYYYMMDD, then:
gcloud sql instances delete apgi-restore-drill-$(date +%Y%m%d)
```

Run this quarterly and record the result (restore time, row-count checksum
comparison) in `docs/RUNBOOKS.md`.
