"""Starts the Strands Decider server for SageMaker AI on port 8080 (or SAGEMAKER_BIND_TO_PORT).

The request body is the decider's own System One request, unchanged:

    {"state": ..., "questions": {id: {"type": "noul"|"choice"|"score", ...}}}

and the response is its System One response. The app itself is in app.py; this launcher only starts
uvicorn, so the parent process never imports torch or the model code. Each of the DECIDER_WORKERS worker
processes loads its own model copy (see app.py for the settings).
"""
import os

import uvicorn

if __name__ == "__main__":
    # SageMaker reaches the container on its network interface, so it must listen on all of them.
    uvicorn.run("app:app", host="0.0.0.0",  # nosec B104
                port=int(os.environ.get("SAGEMAKER_BIND_TO_PORT", "8080")),
                workers=int(os.environ.get("DECIDER_WORKERS", "1")), timeout_keep_alive=75, log_level="warning")
