#!/usr/bin/env python3
"""Bind a NEW PaymentInstrument to an existing key-owned Privy wallet.

Why this exists: setup_payments.py minted a *user-owned* wallet (linked to an
email), so the AgentCore authorization key had no signing authority over it and
Privy rejected at the signing step ("credentials invalid"). We fixed that by
creating a wallet in the Privy dashboard owned by the `aws-key` authorization
key. This script points AgentCore at THAT wallet by passing its walletAddress to
create_payment_instrument, then opens a fresh payment session.

Reuses the existing manager + connector (nothing else needs recreating).
Prints + saves the new instrument/session ids for `cdk deploy`.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import boto3

REGION = os.environ.get("AWS_REGION", "ap-southeast-2")
USER_ID = os.environ.get("AISLE_PAYMENT_USER_ID", "aisle-demo-user")
NETWORK = "ETHEREUM"
# A real inbox you control so the wallet-hub OTP login works. Provide your own.
WALLET_EMAIL = os.environ.get("AISLE_WALLET_EMAIL", "")
SESSION_BUDGET_USDC = "1.00"
SESSION_EXPIRY_MINUTES = 60

RESOURCES_PATH = Path.home() / ".aisle" / "payment-resources.json"


def _load_resource_ids() -> tuple[str, str]:
    """Resolve the existing manager ARN + connector id (env first, then the
    local resources file written by setup_payments.py). Nothing account-specific
    is committed to the repo."""
    mgr_arn = os.environ.get("AISLE_PAYMENT_MANAGER_ARN")
    connector_id = os.environ.get("AISLE_PAYMENT_CONNECTOR_ID")
    if (not mgr_arn or not connector_id) and RESOURCES_PATH.exists():
        saved = json.loads(RESOURCES_PATH.read_text())
        mgr_arn = mgr_arn or saved.get("paymentManagerArn")
        connector_id = connector_id or saved.get("paymentConnectorId")
    if not mgr_arn or not connector_id:
        sys.exit(
            "ERROR: set AISLE_PAYMENT_MANAGER_ARN and AISLE_PAYMENT_CONNECTOR_ID "
            f"(or run setup_payments.py first to populate {RESOURCES_PATH})."
        )
    if not WALLET_EMAIL:
        sys.exit("ERROR: set AISLE_WALLET_EMAIL to an inbox you control (wallet-hub OTP login).")
    return mgr_arn, connector_id


def main() -> None:
    mgr_arn, connector_id = _load_resource_ids()
    sess = boto3.Session(region_name=REGION)
    data = sess.client("bedrock-agentcore")

    print("1. Payment instrument bound to key-owned wallet...")
    inst = data.create_payment_instrument(
        userId=USER_ID,
        paymentManagerArn=mgr_arn,
        paymentConnectorId=connector_id,
        paymentInstrumentType="EMBEDDED_CRYPTO_WALLET",
        paymentInstrumentDetails={"embeddedCryptoWallet": {
            "network": NETWORK,
            "linkedAccounts": [{"email": {"emailAddress": WALLET_EMAIL}}],
        }},
        clientToken=str(uuid.uuid4()),
    )
    pi = inst["paymentInstrument"]
    inst_id = pi["paymentInstrumentId"]
    details = pi.get("paymentInstrumentDetails", {}).get("embeddedCryptoWallet", {})
    wallet_addr = details.get("walletAddress") or ""
    print(f"   {inst_id}  (wallet {wallet_addr})")

    print("2. Payment session...")
    psession = data.create_payment_session(
        userId=USER_ID,
        paymentManagerArn=mgr_arn,
        limits={"maxSpendAmount": {"value": SESSION_BUDGET_USDC, "currency": "USD"}},
        expiryTimeInMinutes=SESSION_EXPIRY_MINUTES,
    )
    session_id = psession["paymentSession"]["paymentSessionId"]
    print(f"   {session_id}")

    print("\nDeploy env (export these, then `cdk deploy AisleToolsStack`):")
    print(f"  export AISLE_PAYMENTS_ENABLED=true")
    print(f"  export AISLE_PAYMENT_MANAGER_ARN={mgr_arn}")
    print(f"  export AISLE_PAYMENT_INSTRUMENT_ID={inst_id}")
    print(f"  export AISLE_PAYMENT_SESSION_ID={session_id}")
    print(f"  export AISLE_PAYMENT_USER_ID={USER_ID}")
    print(f"  export AISLE_PAYTO_ADDRESS={wallet_addr}")

    RESOURCES_PATH.write_text(json.dumps({
        "paymentManagerArn": mgr_arn,
        "paymentConnectorId": connector_id,
        "paymentInstrumentId": inst_id,
        "paymentSessionId": session_id,
        "userId": USER_ID,
        "walletAddress": wallet_addr,
    }, indent=2))
    print(f"\nSaved resource ids to {RESOURCES_PATH}")


if __name__ == "__main__":
    main()
