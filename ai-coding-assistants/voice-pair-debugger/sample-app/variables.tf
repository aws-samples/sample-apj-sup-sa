variable "service" {
  description = "The service name for your resources."
  type        = string
  default     = "voice-demo"
}

variable "region" {
  description = "AWS region to deploy the demo resources into."
  type        = string
  default     = "us-east-1"
}
