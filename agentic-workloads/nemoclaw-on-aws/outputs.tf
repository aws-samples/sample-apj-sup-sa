output "instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.this.id
}

output "ssm_connect" {
  description = "SSM session command"
  value       = "aws ssm start-session --region ${var.bedrock_region} --target ${aws_instance.this.id}"
}

output "ssm_port_forward" {
  description = "SSM port forward command for NemoClaw dashboard"
  value       = "aws ssm start-session --region ${var.bedrock_region} --target ${aws_instance.this.id} --document-name AWS-StartPortForwardingSession --parameters '{\"portNumber\":[\"18789\"],\"localPortNumber\":[\"18789\"]}'"
}
