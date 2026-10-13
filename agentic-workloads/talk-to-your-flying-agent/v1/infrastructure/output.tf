locals {
  instance_ip = var.enable_elastic_ip ? aws_eip.robotics[0].public_ip : aws_instance.robotics.public_ip
}

output "instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.robotics.id
}

output "instance_ip" {
  description = "Public IP address (use Elastic IP if enabled)"
  value       = local.instance_ip
}

output "ssh_command" {
  description = "SSH connection command"
  value       = "ssh -i ~/.ssh/${var.key_name}.pem ${var.instance_user}@${local.instance_ip}"
}

output "backend_url" {
  description = "Backend web UI URL"
  value       = "http://${local.instance_ip}:${var.api_port}"
}

output "dcv_url" {
  description = "Amazon DCV web viewer URL"
  value       = "https://${local.instance_ip}:${var.dcv_port}"
}

output "dcv_login_user" {
  description = "Amazon DCV login user"
  value       = var.instance_user
}

output "project_home" {
  description = "Project directory created on the instance"
  value       = var.project_home
}

output "vscode_ssh_config" {
  description = "Add this to ~/.ssh/config for VS Code Remote SSH"
  value       = <<-EOT

  Host ${var.project_name}
      HostName ${local.instance_ip}
      User ${var.instance_user}
      IdentityFile ~/.ssh/${var.key_name}.pem
  EOT
}
