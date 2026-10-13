#!/bin/sh
# Fetch the video library from S3, then serve. A sync failure is fatal on purpose:
# a task serving an empty library looks healthy but is useless.
set -e

echo "talk2vid: syncing assets from s3://${TALK2VID_S3_BUCKET}/${TALK2VID_S3_PREFIX:-talk2vid}/"
python -m talk2vid.sync_assets

echo "talk2vid: starting server on 0.0.0.0:${TALK2VID_PORT:-7860} (transport=${TALK2VID_TRANSPORT})"
exec python -m uvicorn talk2vid.server:app \
    --host 0.0.0.0 \
    --port "${TALK2VID_PORT:-7860}" \
    --log-level warning \
    --timeout-graceful-shutdown 10
