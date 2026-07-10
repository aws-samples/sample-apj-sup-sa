"""Shared fixtures for CDK stack tests."""

from __future__ import annotations

from typing import Any

import pytest
from aws_cdk import App

from infra.app import StackBundle, build_stacks

TEST_CONTEXT: dict[str, Any] = {
    "environment": "dev",
    "region": "ap-northeast-2",
    "vpc_cidr": "10.0.0.0/16",
    "public_subnet_mask": 24,
    "private_app_subnet_mask": 20,
    "private_data_subnet_mask": 24,
    "aurora_min_capacity": 0.5,
    "aurora_max_capacity": 1.0,
    "aurora_backup_retention_days": 7,
    "aurora_backup_window": "02:00-03:00",
    "aurora_maintenance_window": "Sun:09:00-Sun:10:00",
    "ecr_untagged_retention_days": 30,
    "ecr_tagged_image_count": 10,
    "identity_store_id": "d-1234567890",
    "app_name": "claude-code-proxy",
    "acm_certificate_arn": "arn:aws:acm:ap-northeast-2:123456789012:certificate/test-certificate",
}


@pytest.fixture(scope="session")
def stack_bundle() -> StackBundle:
    """Build the CDK application once for stack assertion tests."""

    app = App(context=TEST_CONTEXT)
    return build_stacks(app, account="123456789012")
