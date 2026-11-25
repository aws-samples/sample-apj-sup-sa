#!/usr/bin/env python3
"""SigV4-signed call to the Bedrock Meter core API.

Usage:
  scripts/api-call.py <METHOD> <PATH> [BODY_JSON]
  scripts/api-call.py GET /usage/me
  scripts/api-call.py GET '/usage/top?period=month&metric=outputTokens&limit=5'
  scripts/api-call.py PUT '/limits/alice' '{"dailyTokenLimit":2000000}'
  scripts/api-call.py POST '/admin/unblock/alice'

Env:
  BEDROCK_METER_API_ENDPOINT  e.g. https://xxxx.execute-api.ap-northeast-2.amazonaws.com
  AWS_REGION                  defaults to ap-northeast-2

Requires: pip install requests requests-aws4auth boto3
"""
import json
import os
import sys

import boto3
import requests
from requests_aws4auth import AWS4Auth

REGION = os.environ.get('AWS_REGION', 'ap-northeast-2')
ENDPOINT = os.environ.get('BEDROCK_METER_API_ENDPOINT', '').rstrip('/')


def call(method: str, path: str, body: str | None = None) -> tuple[int, str]:
    if not ENDPOINT:
        raise SystemExit('BEDROCK_METER_API_ENDPOINT env var required')

    creds = boto3.Session().get_credentials().get_frozen_credentials()
    auth = AWS4Auth(
        creds.access_key, creds.secret_key, REGION, 'execute-api',
        session_token=creds.token,
    )
    headers = {'content-type': 'application/json'} if body else {}
    r = requests.request(method, ENDPOINT + path, auth=auth, data=body, headers=headers, timeout=30)
    return r.status_code, r.text


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    method, path = sys.argv[1], sys.argv[2]
    body = sys.argv[3] if len(sys.argv) > 3 else None
    status, text = call(method, path, body)
    print(f'HTTP {status}')
    try:
        print(json.dumps(json.loads(text), indent=2, ensure_ascii=False))
    except Exception:
        print(text)


if __name__ == '__main__':
    main()
