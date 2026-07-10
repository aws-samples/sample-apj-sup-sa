"""
OTP Authentication via AWS SNS.
Generates OTP, sends via SMS, verifies, and issues JWT tokens.
"""

import random
import time
import logging
from datetime import datetime, timedelta, timezone

import boto3
from jose import jwt

from .config import AWS_REGION, SNS_SENDER_ID, JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRE_MINUTES

logger = logging.getLogger(__name__)

# In-memory OTP store (demo only; use Redis/DynamoDB in production)
_otp_store: dict[str, dict] = {}

# Rate limiting: max OTP requests per phone per window
_rate_limit_store: dict[str, list] = {}
RATE_LIMIT_MAX_REQUESTS = 5
RATE_LIMIT_WINDOW_SECONDS = 300  # 5 minutes

sns_client = boto3.client("sns", region_name=AWS_REGION)

# Set to True to include OTP in response (for local development/demo only)
DEMO_MODE = True


def _check_rate_limit(phone_number: str) -> bool:
    """Return True if the phone number is within rate limits."""
    now = time.time()
    timestamps = _rate_limit_store.get(phone_number, [])
    # Remove expired entries
    timestamps = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW_SECONDS]
    _rate_limit_store[phone_number] = timestamps

    if len(timestamps) >= RATE_LIMIT_MAX_REQUESTS:
        return False
    timestamps.append(now)
    return True


def generate_otp() -> str:
    """Generate a 6-digit OTP."""
    return str(random.randint(100000, 999999))


def send_otp(phone_number: str) -> dict:
    """Generate OTP, store it, and send via SNS SMS."""
    # Rate limit check
    if not _check_rate_limit(phone_number):
        return {"error": "Too many OTP requests. Please try again later.", "retry_after": RATE_LIMIT_WINDOW_SECONDS}

    otp = generate_otp()
    _otp_store[phone_number] = {
        "otp": otp,
        "expires_at": time.time() + 300,  # 5 minutes
        "attempts": 0,
    }

    # Attempt SNS SMS
    try:
        sns_client.publish(
            PhoneNumber=phone_number,
            Message=f"Your Tasty Bites verification code is: {otp}",
            MessageAttributes={
                "AWS.SNS.SMS.SenderID": {
                    "DataType": "String",
                    "StringValue": SNS_SENDER_ID,
                },
                "AWS.SNS.SMS.SMSType": {
                    "DataType": "String",
                    "StringValue": "Transactional",
                },
            },
        )
        logger.info(f"OTP sent via SNS to {phone_number[-4:]}")  # Log only last 4 digits
    except Exception as e:
        logger.warning(f"SNS send failed: {e}")

    response = {"message": "OTP sent", "expires_in": 300}

    # In demo mode, include OTP in response (SNS sandbox drops SMS to unverified numbers)
    if DEMO_MODE:
        response["otp"] = otp
        response["note"] = "Demo mode — OTP included in response. Disable DEMO_MODE for production."

    return response


def verify_otp(phone_number: str, otp_code: str) -> dict | None:
    """Verify OTP and return JWT token if valid."""
    record = _otp_store.get(phone_number)
    if not record:
        return None

    record["attempts"] += 1
    if record["attempts"] > 5:
        del _otp_store[phone_number]
        return None

    if time.time() > record["expires_at"]:
        del _otp_store[phone_number]
        return None

    if record["otp"] != otp_code:
        return None

    # Valid — clean up and issue token
    del _otp_store[phone_number]

    token = jwt.encode(
        {
            "sub": phone_number,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES),
            "iat": datetime.now(timezone.utc),
        },
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )

    return {"access_token": token, "token_type": "bearer", "phone_number": phone_number}


def decode_token(token: str) -> dict | None:
    """Decode and validate JWT token."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except Exception:
        return None
