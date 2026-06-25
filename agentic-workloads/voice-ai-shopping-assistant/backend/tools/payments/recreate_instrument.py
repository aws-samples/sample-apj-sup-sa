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
import uuid
from pathlib import Path

import boto3

REGION = "ap-southeast-2"
USER_ID = "aisle-demo-user"
NETWORK = "ETHEREUM"
WALLET_EMAIL = "james.dinh12@gmail.com"   # real inbox so the wallet hub OTP login works
SESSION_BUDGET_USDC = "1.00"
SESSION_EXPIRY_MINUTES = 60

# Existing AgentCore resources (manager + connector already wired and READY).
MGR_ARN = "arn:aws:bedrock-agentcore:ap-southeast-2:597437436235:payment-manager/aislepma925d5e4-dg3dsbmd1x"
CONNECTOR_ID = "aisleconnfb53c70b-uz8gnrh1iw"

RESOURCES_PATH = Path.home() / ".aisle" / "payment-resources.json"


def main() -> None:
    sess = boto3.Session(region_name=REGION)
    data = sess.client("bedrock-agentcore")

    print("1. Payment instrument bound to key-owned wallet...")
    inst = data.create_payment_instrument(
        userId=USER_ID,
        paymentManagerArn=MGR_ARN,
        paymentConnectorId=CONNECTOR_ID,
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
        paymentManagerArn=MGR_ARN,
        limits={"maxSpendAmount": {"value": SESSION_BUDGET_USDC, "currency": "USD"}},
        expiryTimeInMinutes=SESSION_EXPIRY_MINUTES,
    )
    session_id = psession["paymentSession"]["paymentSessionId"]
    print(f"   {session_id}")

    print("\nDeploy env (export these, then `cdk deploy AisleToolsStack`):")
    print(f"  export AISLE_PAYMENTS_ENABLED=true")
    print(f"  export AISLE_PAYMENT_MANAGER_ARN={MGR_ARN}")
    print(f"  export AISLE_PAYMENT_INSTRUMENT_ID={inst_id}")
    print(f"  export AISLE_PAYMENT_SESSION_ID={session_id}")
    print(f"  export AISLE_PAYMENT_USER_ID={USER_ID}")
    print(f"  export AISLE_PAYTO_ADDRESS={wallet_addr}")

    RESOURCES_PATH.write_text(json.dumps({
        "paymentManagerArn": MGR_ARN,
        "paymentConnectorId": CONNECTOR_ID,
        "paymentInstrumentId": inst_id,
        "paymentSessionId": session_id,
        "userId": USER_ID,
        "walletAddress": wallet_addr,
    }, indent=2))
    print(f"\nSaved resource ids to {RESOURCES_PATH}")


if __name__ == "__main__":
    main()
