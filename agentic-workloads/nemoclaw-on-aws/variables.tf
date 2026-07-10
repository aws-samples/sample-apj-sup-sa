variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "m5.xlarge"
}

variable "volume_size" {
  description = "Root EBS volume size in GB"
  type        = number
  default     = 40
}

variable "bedrock_model_id" {
  description = "Bedrock model ID for LiteLLM config (inference profile recommended)"
  type        = string
  default     = "global.anthropic.claude-opus-4-6-v1"
}

variable "bedrock_region" {
  description = "AWS region for Bedrock API calls"
  type        = string
  default     = "ap-northeast-1"
}

variable "litellm_model_name" {
  description = "Model alias used by NemoClaw (referenced in onboard)"
  type        = string
  default     = "claude-opus"
}

variable "name_prefix" {
  description = "Prefix for resource names"
  type        = string
  default     = "nemoclaw"
}
