provider "google" {
  project = var.project
  region  = var.region
}

terraform {
  backend "gcs" {
  }
}

data "google_project" "project" {}

locals {
  standardised_name    = "${var.env}-${var.app_name}"
  service_ingress_type = "INGRESS_TRAFFIC_ALL"
}

resource "google_service_account" "google_service_account" {
  account_id   = "${local.standardised_name}-svc"
  display_name = "service account"
}

# Create a Storage bucket
resource "google_storage_bucket" "bucket" {
  name          = "${local.standardised_name}-bucket"
  # GCS multi-region: use "US" (or other multi-region like "EU", "ASIA")
  location      = "US"
  force_destroy = true
}

# Grant Storage Admin Role to the Service Account
resource "google_storage_bucket_iam_member" "storage_access" {
  bucket = google_storage_bucket.bucket.name
  role   = "roles/storage.admin"
  member = "serviceAccount:${google_service_account.google_service_account.email}"
}

# Allow the Invoker Service Account to invoke Cloud Run
resource "google_cloud_run_service_iam_member" "invoker" {
  service  = google_cloud_run_v2_service.invoice_extractor.name
  location = google_cloud_run_v2_service.invoice_extractor.location
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.google_service_account.email}"
}

# resource "google_cloud_run_v2_service" "invoice_extractor" {
#   name                = "${local.standardised_name}-svc"
#   launch_stage        = "GA"
#   project             = var.project
#   location            = var.region
#   ingress             = local.service_ingress_type
#   deletion_protection = false

#   template  {
#     timeout                          = "3600s"
#     service_account                  = google_service_account.google_service_account.email
#     max_instance_request_concurrency = var.max_instance_request_concurrency
#     scaling {
#       max_instance_count = var.max_containers
#     }

#     containers {
#       image = "${var.docker_image_name}:${var.image_tag}"
#       ports {
#         container_port = 8000
#       }
#       resources {
#         limits = var.resource_limits
#       }
#       env {
#         name  = "project_id"
#         value = var.project
#       }
#       env {
#         name  = "region_id"
#         value = var.region
#       }
#       env {
#         name  = "env"
#         value = var.env
#       }
#       env {
#         name = "GOOGLE_API_KEY"
#         value_source {
#         secret_key_ref {
#         secret  = google_secret_manager_secret.secret.id
#         version = "latest"
#       }
#     }

#     labels = {
#       cloud_run_services : "mlops"
#     }
#   }
# }

resource "google_cloud_run_v2_service" "invoice_extractor" {
  name                = "${local.standardised_name}-svc"
  launch_stage        = "GA"
  project             = var.project
  location            = var.region
  ingress             = local.service_ingress_type
  deletion_protection = false

  template {
    timeout                          = "3600s"
    service_account                  = google_service_account.google_service_account.email
    max_instance_request_concurrency = var.max_instance_request_concurrency

    scaling {
      max_instance_count = var.max_containers
    }

    containers {
      image = "${var.docker_image_name}:${var.image_tag}"

      ports {
        container_port = 8000
      }

      resources {
        limits = var.resource_limits
      }

      env {
        name  = "project_id"
        value = var.project
      }

      env {
        name  = "region_id"
        value = var.region
      }

      env {
        name  = "env"
        value = var.env
      }

      # Example of adding volume mounts (if you plan to mount secrets as files)
      # volume_mounts {
      #   name       = "my-secret-volume"
      #   mount_path = "/secrets/my-secret"
      #   read_only  = true
      # }
    }

    # Example volumes block (if mounting secrets as files)
    # volumes {
    #   name = "my-secret-volume"
    #   secret {
    #     secret = google_secret_manager_secret.secret.id
    #     items {
    #       version = "latest"
    #       path    = "my-secret"
    #     }
    #   }
    # }

    labels = {
      cloud_run_services = "mlops"
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }
}
