#!/usr/bin/env python3
"""Phase-0 probe: prove a real AgentCore ProcessPayment (x402) succeeds.

Runs the payment leg OUTSIDE Lambda/CDK so we de-risk the money path before
touching any infra. It:
  1. discovers the FUNDED embedded-wallet instrument (the one holding USDC),
  2. opens a fresh payment session (budget + TTL),
  3. calls ProcessPayment with an x402 "exact" requirement that mirrors what
     merchant_api/handler.py emits, trying a couple of payload shapes until the
     live API accepts one (resolves the version "1"/"2", amount/maxAmountRequired
     ambiguity), and
  4. reports the outcome with the right success signals.

WHY the balance probably won't move: ProcessPayment SIGNS an EIP-3009
authorization and returns it as a proof; on-chain settlement only happens when a
merchant/facilitator submits that proof. Our merchant never settles, so the
wallet balance is expected to stay put. The real success signals are:
  - ProcessPayment returns a populated paymentOutput.cryptoX402.payload, and
  - the session's consumed/available budget reflects the charge.

Safe to re-run: each attempt uses a fresh clientToken; charges are 0.01 USDC
against a 1.00 USD session budget. payTo is the funded wallet's OWN address (a
self-authorization) so no testnet funds leave our control even if settled.

Reads resource ids from ~/.aisle/payment-resources.json (manager + connector).
Prints NO secrets.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, ParamValidationError

REGION = "ap-southeast-2"
USER_ID = "aisle-demo-user"

# x402 / chain constants — mirror merchant_api/handler.py.
CHAIN = "BASE_SEPOLIA"                 # GetPaymentInstrumentBalance enum
X402_NETWORK = "base-sepolia"          # x402 payload network id
USDC_ASSET = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"  # USDC on Base Sepolia
AMOUNT_ATOMIC = "10000"                # 0.01 USDC (6 decimals)
SESSION_BUDGET_USDC = "1.00"
SESSION_EXPIRY_MINUTES = 60

RESOURCES_PATH = Path.home() / ".aisle" / "payment-resources.json"


def _load_resources() -> dict:
    if not RESOURCES_PATH.exists():
        raise SystemExit(f"ERROR: {RESOURCES_PATH} not found.")
    return json.loads(RESOURCES_PATH.read_text())


def _usdc(token_balance: dict) -> float:
    return int(token_balance["amount"]) / 10 ** int(token_balance["decimals"])


# Prefer this instrument if it is funded (the wallet we delegated the signer to).
# Both demo wallets may show 20 USDC, but only the delegated one can sign.
PREFER_INSTRUMENT_ID = "payment-instrument-bQpkBdq8vaRgpB1"  # 0x8581581 (delegated)


def find_funded_instrument(data, mgr_arn: str, connector_id: str) -> tuple[str, str, float]:
    """Return (instrument_id, wallet_address, usdc_balance) for the funded wallet."""
    insts = data.list_payment_instruments(paymentManagerArn=mgr_arn, userId=USER_ID)["paymentInstruments"]
    print(f"Found {len(insts)} instrument(s); checking USDC balances on {CHAIN}:")
    best = None  # (balance, inst_id, addr)
    preferred = None  # (balance, inst_id, addr) if PREFER_INSTRUMENT_ID is funded
    for i in insts:
        iid = i["paymentInstrumentId"]
        det = data.get_payment_instrument(
            paymentManagerArn=mgr_arn, paymentInstrumentId=iid, userId=USER_ID,
        )["paymentInstrument"]
        addr = det.get("paymentInstrumentDetails", {}).get("embeddedCryptoWallet", {}).get("walletAddress", "?")
        try:
            tb = data.get_payment_instrument_balance(
                paymentManagerArn=mgr_arn, paymentConnectorId=connector_id,
                paymentInstrumentId=iid, chain=CHAIN, token="USDC", userId=USER_ID,
            )["tokenBalance"]
            bal = _usdc(tb)
        except (ClientError, ParamValidationError) as e:
            bal = -1.0
            print(f"  {iid}  {addr}  balance=ERR {str(e)[:80]}")
            continue
        print(f"  {iid}  {addr}  balance={bal} USDC")
        if best is None or bal > best[0]:
            best = (bal, iid, addr)
        if iid == PREFER_INSTRUMENT_ID and bal > 0:
            preferred = (bal, iid, addr)
    chosen = preferred or best
    if not chosen or chosen[0] <= 0:
        raise SystemExit("ERROR: no instrument holds USDC — fund a wallet first (faucet.circle.com).")
    if preferred:
        print(f"  -> choosing delegated instrument {PREFER_INSTRUMENT_ID}")
    return chosen[1], chosen[2], chosen[0]


def _candidate_payloads(pay_to: str) -> list[tuple[str, dict]]:
    """x402 payload shapes to try, most-likely first.

    The merchant emits x402Version=1 with the full `accepts[0]` object, so shape
    A mirrors that faithfully. B is the shape the current handler sends today
    (version "2" + `amount`); we try it so we learn whether it also works.
    """
    full = {
        "scheme": "exact",
        "network": X402_NETWORK,
        "maxAmountRequired": AMOUNT_ATOMIC,
        "resource": "https://merchant.local/",
        "description": "Aisle grocery order placement (probe)",
        "mimeType": "application/json",
        "payTo": pay_to,
        "maxTimeoutSeconds": 300,
        "asset": USDC_ASSET,
        "extra": {"name": "USDC", "version": "2"},
    }
    handler_shape = dict(full)
    handler_shape["amount"] = handler_shape.pop("maxAmountRequired")
    for k in ("resource", "description", "mimeType"):
        handler_shape.pop(k, None)
    return [
        ("A: version=1, full accepts[0] (maxAmountRequired)", {"version": "1", "payload": full}),
        ("B: version=2, current-handler shape (amount)", {"version": "2", "payload": handler_shape}),
    ]


def try_process_payment(data, mgr_arn, session_id, instrument_id, pay_to):
    last_err = None
    for label, crypto in _candidate_payloads(pay_to):
        print(f"\n--- ProcessPayment attempt [{label}] ---")
        try:
            resp = data.process_payment(
                userId=USER_ID,
                paymentManagerArn=mgr_arn,
                paymentSessionId=session_id,
                paymentInstrumentId=instrument_id,
                paymentType="CRYPTO_X402",
                paymentInput={"cryptoX402": crypto},
                clientToken=str(uuid.uuid4()),
            )
            resp.pop("ResponseMetadata", None)
            print("  SUCCESS")
            return label, crypto, resp
        except (ClientError, ParamValidationError) as e:
            last_err = e
            print(f"  FAILED: {type(e).__name__}: {str(e)[:300]}")
    return None, None, last_err


def main() -> None:
    res = _load_resources()
    mgr_arn = res["paymentManagerArn"]
    connector_id = res["paymentConnectorId"]
    data = boto3.Session(region_name=REGION).client("bedrock-agentcore")

    print("=" * 72)
    print("Phase-0 ProcessPayment probe")
    print("=" * 72)
    print(f"manager:   {mgr_arn}")
    print(f"connector: {connector_id}\n")

    inst_id, wallet_addr, bal_before = find_funded_instrument(data, mgr_arn, connector_id)
    print(f"\nUsing funded instrument {inst_id} ({wallet_addr}), balance {bal_before} USDC")
    pay_to = wallet_addr  # self-authorization: funds stay with us even if settled

    print("\nCreating a fresh payment session...")
    sess = data.create_payment_session(
        userId=USER_ID,
        paymentManagerArn=mgr_arn,
        limits={"maxSpendAmount": {"value": SESSION_BUDGET_USDC, "currency": "USD"}},
        expiryTimeInMinutes=SESSION_EXPIRY_MINUTES,
    )["paymentSession"]
    session_id = sess["paymentSessionId"]
    print(f"  session {session_id}")

    label, crypto, result = try_process_payment(data, mgr_arn, session_id, inst_id, pay_to)

    print("\n" + "=" * 72)
    if label:
        print(f"RESULT: ProcessPayment SUCCEEDED with payload shape [{label}]")
        print(f"  status:          {result.get('status')}")
        print(f"  processPaymentId:{result.get('processPaymentId')}")
        out = result.get("paymentOutput", {})
        print("  paymentOutput:")
        print("    " + json.dumps(out, default=str, indent=2).replace("\n", "\n    "))
        # Secondary signal: session budget consumption.
        try:
            s = data.get_payment_session(paymentManagerArn=mgr_arn, paymentSessionId=session_id, userId=USER_ID)
            print("\n  session after payment:")
            print("    " + json.dumps(s.get("paymentSession", s), default=str, indent=2).replace("\n", "\n    "))
        except (ClientError, ParamValidationError) as e:
            print(f"  (could not re-read session: {str(e)[:120]})")
        # Informational: balance (expected unchanged — no settlement).
        try:
            tb = data.get_payment_instrument_balance(
                paymentManagerArn=mgr_arn, paymentConnectorId=connector_id,
                paymentInstrumentId=inst_id, chain=CHAIN, token="USDC", userId=USER_ID,
            )["tokenBalance"]
            print(f"\n  wallet balance after: {_usdc(tb)} USDC (before {bal_before}; "
                  f"unchanged is expected — proof not settled on-chain)")
        except (ClientError, ParamValidationError):
            pass
        print("\n  >> Carry this winning payload shape into create_order._pay_x402.")
        print(f"  >> Winning cryptoX402: version={crypto['version']}, "
              f"keys={sorted(crypto['payload'].keys())}")
    else:
        print("RESULT: ProcessPayment FAILED on all payload shapes.")
        print(f"  last error: {type(result).__name__}: {result}")
        print("\n  How to read this:")
        print("   - 'credentials invalid' / 'unauthorized' / signer errors  -> the funded")
        print("     wallet is NOT delegated to the aws-key signer. Fix via WalletHub")
        print("     delegation (samples 03) or fund a fresh key-owned wallet (faucet).")
        print("   - ValidationException on the payload  -> payload-shape issue; adjust fields.")
        print("   - budget / limit errors  -> session maxSpendAmount too low.")
    print("=" * 72)


if __name__ == "__main__":
    main()
