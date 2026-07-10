"""Container runtime — what runs inside each Batch job."""
from .entrypoint import main, process_shard
from .s3_io import S3Uri, iter_input_records
from .vllm_driver import DriverStats, InferenceResult, drive_inference, wait_for_vllm_ready

__all__ = [
    "DriverStats",
    "InferenceResult",
    "S3Uri",
    "drive_inference",
    "iter_input_records",
    "main",
    "process_shard",
    "wait_for_vllm_ready",
]
