#!/usr/bin/env python3
"""One-time setup for AgentCore Payments (Stripe Privy provider).

Creates, in order:
  1. IAM service role the payment manager assumes
  2. PaymentCredentialProvider  (your Privy creds -> Secrets Manager via Identity)
  3. PaymentManager             (top-level coordinator, AWS_IAM authorizer)
  4. PaymentConnector           (links manager -> Privy creds)
  5. PaymentInstrument          (embedded crypto wallet; prints a funding URL)
  6. PaymentSession             (scoped budget/TTL for create_order to spend in)

Reads Privy credentials from ~/.aisle/privy-creds.json (NOT in the repo):
  {"appId","appSecret","authorizationId","authorizationPrivateKey"}

Prints the env values to pass to `cdk deploy` so create_order runs the real
x402 leg:
  AISLE_PAYMENTS_ENABLED=true
  AISLE_PAYMENT_MANAGER_ARN=...
  AISLE_PAYMENT_INSTRUMENT_ID=...
  AISLE_PAYMENT_SESSION_ID=...
  AISLE_PAYMENT_USER_ID=...
  AISLE_PAYTO_ADDRESS=<funded wallet address>   (for the merchant payTo)

MANUAL STEP: after this prints the funding URL, open it in a browser, top up the
testnet wallet with USDC, and grant the agent delegated signing. Only then can
ProcessPayment succeed.

Idempotent-ish: re-running creates new resources. For a demo just run once and
keep the printed ids. Region/account come from your AWS profile.
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

import boto3

REGION = "ap-southeast-2"
USER_ID = "aisle-demo-user"
WALLET_EMAIL = "aisle-demo@example.com"   # linked account for the embedded wallet
NETWORK = "ETHEREUM"                        # wallet network enum (testnet chosen in x402 payload)
SESSION_BUDGET_USDC = "1.00"               # spend cap across the session
SESSION_EXPIRY_MINUTES = 60

CREDS_PATH = Path.home() / ".aisle" / "privy-creds.json"


def load_creds() -> dict:
    if not CREDS_PATH.exists():
        sys.exit(f"ERROR: {CREDS_PATH} not found. Create it with your Privy creds first.")
    creds = json.loads(CREDS_PATH.read_text())
    required = ["appId", "appSecret", "authorizationId", "authorizationPrivateKey"]
    missing = [k for k in required if not creds.get(k)]
    if missing:
        sys.exit(f"ERROR: {CREDS_PATH} missing keys: {missing}")
    return creds


def ensure_service_role(iam, account_id: str) -> str:
    """Create (or reuse) the IAM role the payment manager assumes."""
    role_name = "AislePaymentManagerRole"
    trust = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    }
    try:
        r = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="Role AgentCore Payments manager assumes (Aisle demo)",
        )
        arn = r["Role"]["Arn"]
        # Broad inline policy to keep this setup script simple. FOR PRODUCTION:
        # scope to least privilege — replace "bedrock-agentcore:*" with the
        # specific payment actions you call (e.g. CreatePaymentInstrument,
        # CreatePaymentSession, ProcessPayment) and scope "Resource" to your
        # payment-manager ARN and the specific secret ARN instead of "*".
        iam.put_role_policy(
            RoleName=role_name, PolicyName="payments",
            PolicyDocument=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Action": ["bedrock-agentcore:*", "secretsmanager:GetSecretValue"],
                    "Resource": "*",
                }],
            }),
        )
        print(f"  created role {role_name}")
        time.sleep(10)  # let the role propagate before the manager assumes it
        return arn
    except iam.exceptions.EntityAlreadyExistsException:
        arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
        print(f"  reusing role {role_name}")
        return arn


def main() -> None:
    creds = load_creds()
    sess = boto3.Session(region_name=REGION)
    sts = sess.client("sts")
    account_id = sts.get_caller_identity()["Account"]
    iam = sess.client("iam")
    ctrl = sess.client("bedrock-agentcore-control")
    data = sess.client("bedrock-agentcore")

    print("1. IAM service role...")
    role_arn = ensure_service_role(iam, account_id)

    print("2. Privy credential provider...")
    cp = ctrl.create_payment_credential_provider(
        name=f"aisleprivy{uuid.uuid4().hex[:8]}",
        credentialProviderVendor="StripePrivy",
        providerConfigurationInput={
            "stripePrivyConfiguration": {
                "appId": creds["appId"],
                "appSecret": creds["appSecret"],
                "authorizationId": creds["authorizationId"],
                "authorizationPrivateKey": creds["authorizationPrivateKey"],
            },
        },
    )
    cp_arn = cp["credentialProviderArn"]
    print(f"   {cp_arn}")

    print("3. Payment manager...")
    mgr = ctrl.create_payment_manager(
        name=f"aislepm{uuid.uuid4().hex[:8]}",
        authorizerType="AWS_IAM",
        roleArn=role_arn,
    )
    mgr_arn = mgr["paymentManagerArn"]
    mgr_id = mgr["paymentManagerId"]
    while True:
        st = ctrl.get_payment_manager(paymentManagerId=mgr_id)["status"]
        if st == "READY":
            break
        print(f"   manager status: {st}...")
        time.sleep(5)
    print(f"   {mgr_arn}")

    print("4. Payment connector...")
    conn = ctrl.create_payment_connector(
        paymentManagerId=mgr_id,
        name=f"aisleconn{uuid.uuid4().hex[:8]}",
        type="StripePrivy",
        credentialProviderConfigurations=[
            {"stripePrivy": {"credentialProviderArn": cp_arn}},
        ],
    )
    print(f"   {conn['paymentConnectorId']}")

    print("5. Payment instrument (embedded wallet)...")
    inst = data.create_payment_instrument(
        userId=USER_ID,
        paymentManagerArn=mgr_arn,
        paymentConnectorId=conn["paymentConnectorId"],
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
    redirect = details.get("redirectUrl")
    wallet_addr = details.get("walletAddress") or ""
    print(f"   {inst_id}")

    print("6. Payment session...")
    psession = data.create_payment_session(
        userId=USER_ID,
        paymentManagerArn=mgr_arn,
        limits={"maxSpendAmount": {"value": SESSION_BUDGET_USDC, "currency": "USD"}},
        expiryTimeInMinutes=SESSION_EXPIRY_MINUTES,
    )
    session_id = psession["paymentSession"]["paymentSessionId"]
    print(f"   {session_id}")

    print("\n" + "=" * 70)
    print("MANUAL STEP — fund the wallet, then it can pay:")
    print(f"  Open: {redirect}")
    print("  Top up testnet USDC + grant the agent delegated signing.")
    print("=" * 70)
    print("\nDeploy env (export these, then `cdk deploy AisleToolsStack`):")
    print(f"  export AISLE_PAYMENTS_ENABLED=true")
    print(f"  export AISLE_PAYMENT_MANAGER_ARN={mgr_arn}")
    print(f"  export AISLE_PAYMENT_INSTRUMENT_ID={inst_id}")
    print(f"  export AISLE_PAYMENT_SESSION_ID={session_id}")
    print(f"  export AISLE_PAYMENT_USER_ID={USER_ID}")
    if wallet_addr:
        print(f"  export AISLE_PAYTO_ADDRESS={wallet_addr}")
    else:
        print("  # wallet address not in response — set AISLE_PAYTO_ADDRESS from the wallet hub")

    # Persist for convenience (outside repo).
    out = CREDS_PATH.parent / "payment-resources.json"
    out.write_text(json.dumps({
        "paymentManagerArn": mgr_arn, "paymentInstrumentId": inst_id,
        "paymentSessionId": session_id, "userId": USER_ID,
        "walletAddress": wallet_addr, "redirectUrl": redirect,
    }, indent=2))
    print(f"\nSaved resource ids to {out}")


if __name__ == "__main__":
    main()
