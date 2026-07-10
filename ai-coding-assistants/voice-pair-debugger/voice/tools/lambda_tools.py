import json

from voice.config import get_boto3_session


async def describe_lambda_function(params, function_name: str):
    """Describe a Lambda function's configuration.

    Args:
        function_name: The Lambda function name (e.g. voice-demo-get-users).
    """
    try:
        session = get_boto3_session()
        lambda_client = session.client("lambda")

        config = lambda_client.get_function_configuration(FunctionName=function_name)

        summary = {
            "function_name": config["FunctionName"],
            "runtime": config.get("Runtime"),
            "handler": config.get("Handler"),
            "memory_mb": config.get("MemorySize"),
            "timeout_s": config.get("Timeout"),
            "last_modified": config.get("LastModified"),
            "environment": config.get("Environment", {}).get("Variables", {}),
        }

        await params.result_callback(summary)
    except Exception as e:
        await params.result_callback({"error": str(e)})
