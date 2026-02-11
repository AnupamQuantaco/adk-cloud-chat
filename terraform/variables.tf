variable "project" {
  type        = string
  description = "the ID of the GCP project to use"
}

variable "region" {
  type        = string
  description = "GCP region to use"
}

variable "env" {
  type        = string
  description = "environment name for the deployment"
}

variable "app_name" {
  type        = string
  description = "name of the application"
}

variable "docker_image_name" {
  type        = string
  description = "name of the Docker image to deploy"
}

variable "max_instance_request_concurrency" {
  type        = string
  description = "number of concurrent requests"
  default     = 5
}

variable "max_containers" {
  type        = string
  description = "number of concurrent instances"
  default     = 5
}

variable "image_tag" {
  type        = string
  description = "tag for image"
  default     = ""
}

variable "resource_limits" {
  type        = map(string)
  description = "machine resources to deploy the container with"
  default     = {
    cpu    : "6"
    memory : "4Gi"
  }
}
