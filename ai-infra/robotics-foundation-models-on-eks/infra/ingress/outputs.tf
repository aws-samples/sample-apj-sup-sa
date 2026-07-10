output "domain_name" {
  description = "Domain name for the OSMO admin UI."
  value       = local.domain_name
}

output "osmo_admin_url" {
  description = "HTTPS URL for the OSMO admin UI."
  value       = "https://${local.domain_name}"
}

output "certificate_arn" {
  description = "Validated ACM certificate ARN."
  value       = aws_acm_certificate_validation.osmo_admin.certificate_arn
}

output "load_balancer_dns_name" {
  description = "ALB DNS name."
  value       = data.aws_lb.osmo_admin.dns_name
}

output "load_balancer_arn" {
  description = "ALB ARN."
  value       = data.aws_lb.osmo_admin.arn
}

output "route53_record_fqdn" {
  description = "Route 53 record FQDN."
  value       = aws_route53_record.osmo_admin.fqdn
}

output "ingress_name" {
  description = "Kubernetes Ingress name."
  value       = kubernetes_ingress_v1.osmo_admin.metadata[0].name
}
