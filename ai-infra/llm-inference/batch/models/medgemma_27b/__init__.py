"""MedGemma-27B configuration for AWS Batch deployment."""
from .batch_plans import (
    g7e_family_spot_with_od_failover,
    g7e_spot_single_queue,
    p4d_spot_and_on_demand_failover,
    p4d_spot_single_queue,
)
from .model_spec import MEDGEMMA_27B

__all__ = [
    "MEDGEMMA_27B",
    "g7e_family_spot_with_od_failover",
    "g7e_spot_single_queue",
    "p4d_spot_and_on_demand_failover",
    "p4d_spot_single_queue",
]
