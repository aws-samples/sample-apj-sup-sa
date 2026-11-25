English | [한국어](SECURITY.ko.md)

# Security

This repository is a learning example that uses a public dataset. Do not add real customer data or production credentials without a separate security review.

## Basic principles

- Do not store AWS credentials in source code, `.env`, or logs.
- Keep S3 Block Public Access enabled.
- The Training role and the MLflow role use only the S3 prefixes they need.
- Review KMS encryption, VPC endpoints, network isolation, and retention policies before using real data.
- Clean up the MLflow App and S3 artifacts when they are no longer needed.

Report security issues through your organization's approved security channel, without including credentials or real data.
