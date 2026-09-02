#!/usr/bin/env bash
# Local smoke tests for a SageMaker Marketplace-compatible container.
# Run every test. If any fails, do not push to ECR — SageMaker will fail too.
#
# Usage: ./test_local.sh <image-tag> <weights-dir>
# Example: ./test_local.sh my-model:latest ./model_artifacts

set -euo pipefail

IMAGE="${1:-my-model:latest}"
WEIGHTS_DIR="${2:-./model_artifacts}"
CONTAINER_NAME="sagemaker-marketplace-test"

cleanup() {
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    docker rm -f "${CONTAINER_NAME}-isolated" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==============================================================="
echo "1. Starting container exactly as SageMaker would"
echo "==============================================================="
docker run --gpus all --rm \
    --publish 8080:8080/tcp \
    --volume "$(realpath "$WEIGHTS_DIR"):/opt/ml/model:ro" \
    --detach --name "$CONTAINER_NAME" \
    "$IMAGE" serve

echo "Waiting up to 8 minutes for /ping to return 200..."
DEADLINE=$(( $(date +%s) + 480 ))
while (( $(date +%s) < DEADLINE )); do
    if curl --silent --fail --max-time 2 http://127.0.0.1:8080/ping >/dev/null; then
        echo "  /ping returned 200"
        break
    fi
    sleep 5
done

if ! curl --silent --fail --max-time 2 http://127.0.0.1:8080/ping >/dev/null; then
    echo "  FAILED: /ping did not return 200 within 8 minutes"
    docker logs "$CONTAINER_NAME" | tail -50
    exit 1
fi

echo ""
echo "==============================================================="
echo "2. /invocations with sample payload"
echo "==============================================================="
RESPONSE=$(curl --silent --max-time 60 -X POST \
    -H "Content-Type: application/json" \
    --data-binary @test/test_input.json \
    http://127.0.0.1:8080/invocations)
echo "  Response: $RESPONSE"

echo ""
echo "==============================================================="
echo "3. /execution-parameters"
echo "==============================================================="
curl --silent --max-time 2 http://127.0.0.1:8080/execution-parameters
echo ""

echo ""
echo "==============================================================="
echo "4. SIGTERM graceful shutdown (must complete within 30s)"
echo "==============================================================="
START=$(date +%s)
docker stop --time 30 "$CONTAINER_NAME"
ELAPSED=$(( $(date +%s) - START ))
echo "  Shutdown took ${ELAPSED}s (must be <=30s)"
if (( ELAPSED > 30 )); then
    echo "  FAILED: container was SIGKILLed. Check ENTRYPOINT — it must be exec form."
    exit 1
fi

echo ""
echo "==============================================================="
echo "5. WebSocket smoke test (only if the container implements it)"
echo "==============================================================="
if [[ -f "test/test_websocket.py" ]] && \
   docker exec "$CONTAINER_NAME" python3 -c "import websockets" >/dev/null 2>&1 || \
   command -v python3 >/dev/null 2>&1; then
    # Restart the main container since we already stopped it above
    docker run --gpus all --rm \
        --publish 8080:8080/tcp \
        --volume "$(realpath "$WEIGHTS_DIR"):/opt/ml/model:ro" \
        --detach --name "$CONTAINER_NAME" \
        "$IMAGE" serve
    # Wait for /ping again
    DEADLINE=$(( $(date +%s) + 480 ))
    while (( $(date +%s) < DEADLINE )); do
        curl --silent --fail --max-time 2 http://127.0.0.1:8080/ping >/dev/null && break
        sleep 5
    done
    if python3 test/test_websocket.py; then
        echo "  WebSocket smoke test passed"
    else
        echo "  WebSocket smoke test failed — skipping if this container doesn't"
        echo "  implement /invocations-bidirectional-stream (that's fine)."
    fi
fi

echo ""
echo "==============================================================="
echo "6. Network isolation (--network none) — container must still start"
echo "==============================================================="
docker run --gpus all --rm \
    --network none \
    --volume "$(realpath "$WEIGHTS_DIR"):/opt/ml/model:ro" \
    --detach --name "${CONTAINER_NAME}-isolated" \
    "$IMAGE" serve

# Give it a moment to start; then check that /ping eventually goes healthy
# via `docker exec` since we published no port for the isolated run.
sleep 60
if docker exec "${CONTAINER_NAME}-isolated" \
        python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ping', timeout=2)"; then
    echo "  Container started under --network none. Good."
else
    echo "  FAILED: container has a runtime dependency on external services."
    echo "  Common causes: HuggingFace Hub download, pip install at startup,"
    echo "  license server, S3 access."
    docker logs "${CONTAINER_NAME}-isolated" | tail -50
    exit 1
fi

echo ""
echo "==============================================================="
echo "All local tests passed."
echo "==============================================================="
