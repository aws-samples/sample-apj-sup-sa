"""Cognito auth gate. Production-only; in development this is a no-op."""
from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass
from typing import Optional

import boto3
import jwt
import streamlit as st
from botocore.exceptions import BotoCoreError, ClientError

from config import CONFIG


@dataclass(frozen=True)
class AuthedUser:
    sub: str    # Cognito user id (UUID), used as quota key
    email: str  # id_token removed — no reason to keep it in session state


def _client():
    return boto3.client("cognito-idp", region_name=CONFIG.cognito_region)


def _initiate_auth(email: str, password: str) -> dict:
    return _client().initiate_auth(
        AuthFlow="USER_PASSWORD_AUTH",
        ClientId=CONFIG.cognito_client_id,
        AuthParameters={"USERNAME": email, "PASSWORD": password},
    )


def _respond_to_challenge(challenge: str, session: str, email: str, new_password: str) -> dict:
    return _client().respond_to_auth_challenge(
        ClientId=CONFIG.cognito_client_id,
        ChallengeName=challenge,
        Session=session,
        ChallengeResponses={"USERNAME": email, "NEW_PASSWORD": new_password},
    )


def _revoke_token(refresh_token: str) -> None:
    try:
        _client().revoke_token(Token=refresh_token, ClientId=CONFIG.cognito_client_id)
    except (ClientError, BotoCoreError):
        pass  # best-effort; session state is cleared regardless


@st.cache_data(ttl=3600, show_spinner=False)
def _get_jwks() -> dict:
    url = (
        f"https://cognito-idp.{CONFIG.cognito_region}.amazonaws.com"
        f"/{CONFIG.cognito_user_pool_id}/.well-known/jwks.json"
    )
    with urllib.request.urlopen(url, timeout=5) as r:  # noqa: S310 — fixed Cognito URL, not user input
        return json.loads(r.read())


def _decode_user(id_token: str) -> Optional[AuthedUser]:
    """Verify Cognito RS256 JWT signature against the pool's JWKS, then extract claims."""
    try:
        jwks = _get_jwks()
        header = jwt.get_unverified_header(id_token)
        key_data = next((k for k in jwks["keys"] if k["kid"] == header["kid"]), None)
        if key_data is None:
            return None
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key_data))
        claims = jwt.decode(
            id_token,
            public_key,
            algorithms=["RS256"],
            audience=CONFIG.cognito_client_id,
        )
        return AuthedUser(sub=claims["sub"], email=claims.get("email", ""))
    except Exception:  # noqa: BLE001
        return None


def _complete_auth(tokens: dict) -> Optional[AuthedUser]:
    id_token = tokens.get("IdToken")
    refresh_token = tokens.get("RefreshToken")
    if not id_token:
        return None
    user = _decode_user(id_token)
    if user and refresh_token:
        st.session_state["refresh_token"] = refresh_token
    return user


def require_login() -> AuthedUser:
    """Block until the user has a valid session. In dev mode, return a fake user."""
    if not CONFIG.is_production:
        return AuthedUser(sub="dev-local", email="dev@local")

    if not (CONFIG.cognito_user_pool_id and CONFIG.cognito_client_id):
        st.error(
            "Production deploy is misconfigured: COGNITO_USER_POOL_ID / COGNITO_CLIENT_ID env vars are missing."
        )
        st.stop()

    if st.session_state.get("authed_user"):
        return st.session_state["authed_user"]

    st.markdown("# Bedrock Model Benchmarking")

    # ---- New-password challenge (admin-created users) ----
    pending = st.session_state.get("pending_challenge")
    if pending:
        st.markdown("### set a new password")
        st.caption("first-time sign-in requires a new password.")
        with st.form("new_password_form"):
            new_password = st.text_input("new password", type="password")
            confirm = st.text_input("confirm new password", type="password")
            go = st.form_submit_button("set password", type="primary")
        if go:
            if new_password != confirm:
                st.error("passwords do not match.")
            else:
                try:
                    resp = _respond_to_challenge(
                        "NEW_PASSWORD_REQUIRED",
                        pending["session"],
                        pending["email"],
                        new_password,
                    )
                except (ClientError, BotoCoreError) as e:
                    st.error(f"failed to set password: {e}")
                    st.stop()
                user = _complete_auth(resp.get("AuthenticationResult", {}))
                if not user:
                    st.error("could not verify Cognito token.")
                    st.stop()
                st.session_state.authed_user = user
                del st.session_state["pending_challenge"]
                st.rerun()
        st.stop()

    # ---- Sign-in ----
    with st.form("login_form"):
        email = st.text_input("email")
        password = st.text_input("password", type="password")
        submit = st.form_submit_button("sign in", type="primary")
    if submit:
        try:
            resp = _initiate_auth(email, password)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("NotAuthorizedException", "UserNotFoundException"):
                st.error("invalid email or password.")
            else:
                st.error(f"sign-in failed: {e}")
            st.stop()
        except BotoCoreError as e:
            st.error(f"sign-in failed: {e}")
            st.stop()
        if resp.get("ChallengeName") == "NEW_PASSWORD_REQUIRED":
            st.session_state.pending_challenge = {"session": resp["Session"], "email": email}
            st.rerun()
        else:
            user = _complete_auth(resp.get("AuthenticationResult", {}))
            if not user:
                st.error("could not verify Cognito token.")
                st.stop()
            st.session_state.authed_user = user
            st.session_state.session_started_at = time.time()
            st.rerun()

    st.stop()


def render_logout_button() -> None:
    if not CONFIG.is_production:
        return
    user = st.session_state.get("authed_user")
    if user and st.sidebar.button("sign out", use_container_width=True):
        refresh_token = st.session_state.get("refresh_token")
        if refresh_token:
            _revoke_token(refresh_token)
        for key in ("authed_user", "refresh_token", "session_started_at", "pending_challenge"):
            st.session_state.pop(key, None)
        st.rerun()
