# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Drive the deployed web app in Chromium as a real user (hosted UI sign-in with TOTP)."""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Page, expect

from session import Deployment, TestUser, fresh_totp


def sign_in(page: Page, d: Deployment, user: TestUser) -> None:
    page.goto(d.app_url)
    # A signed-out visit may go straight to the hosted sign-in page, or first show the app's own
    # "Sign in" button: accept either.
    button = page.get_by_role("button", name="Sign in")
    hosted = page.locator('input[name="username"]:visible')
    button.or_(hosted).first.wait_for(timeout=30000)
    if "amazoncognito.com" not in page.url and button.is_visible():
        button.click()
    page.wait_for_url("**amazoncognito.com/**", timeout=30000)
    page.locator('input[name="username"]:visible').fill(user.email)
    page.locator('input[name="password"]:visible').fill(user.password)
    page.locator('input[name="signInSubmitButton"]:visible').click()
    code_box = page.locator('input[name="totpCode"]:visible, input#totpCodeInput:visible, input[name="code"]:visible')
    code_box.first.wait_for(timeout=30000)
    if not user.totp_secret:
        raise RuntimeError("sign in once through the API helper first: it enrols TOTP")
    code_box.first.fill(fresh_totp(user.totp_secret))
    page.keyboard.press("Enter")
    page.wait_for_url(f"{d.app_url}/**", timeout=30000)
    expect(page.get_by_text("Overview").first).to_be_visible(timeout=30000)


def console_errors(page: Page) -> list[str]:
    errors: list[str] = []

    def record(msg: Any) -> None:
        if msg.type == "error":
            errors.append(msg.text)

    page.on("console", record)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    return errors
