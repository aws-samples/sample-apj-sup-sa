[English](SECURITY.md) | 한국어

# Security

이 repository는 public dataset을 사용하는 학습 예제입니다. 별도의 security 검토 없이 실제 customer data나 production credential을 추가하지 않습니다.

## 기본 원칙

- AWS credential을 source, `.env`, log에 저장하지 않습니다.
- S3 Block Public Access를 유지합니다.
- Training role과 MLflow role은 필요한 S3 prefix만 사용합니다.
- 실제 data를 사용하기 전에 KMS encryption, VPC endpoint, network isolation, retention policy를 검토합니다.
- MLflow App과 S3 artifact가 더 이상 필요하지 않으면 정리합니다.

security 문제에는 credential이나 실제 data를 포함하지 말고 조직의 승인된 security channel로 신고합니다.
