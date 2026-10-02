terraform {
  required_providers { google = { source = "hashicorp/google", version = "~> 6.0" } }
}
variable "project_id" { type = string }
variable "region" {
  type = string
  default = "asia-southeast1"
}
variable "image_uri" { type = string }
variable "private_data_bucket" { type = string }
variable "database_secret" { type = string }
variable "api_token_secret" { type = string }
provider "google" {
  project = var.project_id
  region = var.region
}
resource "google_service_account" "model" {
  account_id = "sabah-rail-model"
  display_name = "Sabah Rail model runtime"
}
resource "google_storage_bucket_iam_member" "data_read" {
  bucket = var.private_data_bucket
  role = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.model.email}"
}
resource "google_secret_manager_secret_iam_member" "db_read" {
  project = var.project_id
  secret_id = var.database_secret
  role = "roles/secretmanager.secretAccessor"
  member = "serviceAccount:${google_service_account.model.email}"
}
resource "google_secret_manager_secret_iam_member" "token_read" {
  project = var.project_id
  secret_id = var.api_token_secret
  role = "roles/secretmanager.secretAccessor"
  member = "serviceAccount:${google_service_account.model.email}"
}
resource "google_cloud_run_v2_service" "model" {
  name = "sabah-rail-model"
  location = var.region
  deletion_protection = true
  template {
    service_account = google_service_account.model.email
    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }
    volumes {
      name = "project-data"
      gcs {
        bucket = var.private_data_bucket
        read_only = true
      }
    }
    containers {
      image = var.image_uri
      resources {
        limits = { cpu = "1", memory = "512Mi" }
        cpu_idle = true
      }
      ports { container_port = 8080 }
      volume_mounts {
        name = "project-data"
        mount_path = "/private-data"
      }
      env {
        name = "MODEL_DATA_PATH"
        value = "/private-data/project.json"
      }
      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret = var.database_secret
            version = "latest"
          }
        }
      }
      env {
        name = "MODEL_API_TOKEN"
        value_source {
          secret_key_ref {
            secret = var.api_token_secret
            version = "latest"
          }
        }
      }
    }
  }
}
# No allUsers binding: access stays private. Add IAP/approved user IAM only after auth review.
# Database is deliberately not provisioned here: select approved Cloud SQL or
# managed PostgreSQL endpoint, verify PostGIS, backups, region and cost first.
