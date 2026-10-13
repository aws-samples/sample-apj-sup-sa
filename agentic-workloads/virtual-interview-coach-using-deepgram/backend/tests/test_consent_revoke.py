"""FR-220 consent-revoke tests: revoking materials consent must purge the RAW S3 resume object too.

The bug: PUT /me/consent let db.set_consent NULL resume_uri FIRST, then fetched the user (uri now
NULL) and deleted nothing — the raw resume survived in S3 until DELETE /me. The handler must capture
the uri BEFORE the row update and delete the S3 object BEFORE it (privacy-safe ordering: a failed S3
delete leaves the uri in place so the revoke is re-runnable, never an untracked orphan).

Offline: the auth, db, and S3 seams are stubbed at the consent-module namespace (no DB, no AWS).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


def _wire(monkeypatch, *, resume_uri):
    """Stub the consent module's seams; returns the call journal."""
    from src.api import consent

    journal: list = []
    user_row = {"user_sub": "sub-1", "resume_uri": resume_uri}

    async def fake_validate(_authorization):
        return "sub-1"

    async def fake_get_user(sub):
        journal.append(("get_user", sub))
        return dict(user_row)

    async def fake_set_consent(sub, consent_store_materials, retention_days):
        journal.append(("set_consent", consent_store_materials))
        if not consent_store_materials:
            user_row["resume_uri"] = None  # the real set_consent NULLs the uri
        return {"consent_recording": consent_store_materials,
                "retention_days": retention_days, "consent_recording_at": None}

    def fake_delete_resume(uri):
        journal.append(("delete_resume", uri))
        return 1 if uri else 0

    monkeypatch.setattr(consent, "validate_token", fake_validate)
    monkeypatch.setattr(consent.db, "get_user_by_sub", fake_get_user)
    monkeypatch.setattr(consent.db, "set_consent", fake_set_consent)
    monkeypatch.setattr(consent, "delete_resume", fake_delete_resume)
    return journal


async def test_revoke_deletes_s3_resume_with_pre_null_uri(monkeypatch):
    from src.api import consent

    journal = _wire(monkeypatch, resume_uri="s3://resume-bkt/resumes/sub-1.pdf")
    body = await consent.put_consent(authorization="Bearer t",
                                     consent_store_materials=False, retention_days=30)
    # The S3 delete saw the REAL uri (captured before set_consent NULLed it)...
    assert ("delete_resume", "s3://resume-bkt/resumes/sub-1.pdf") in journal
    # ...and ran BEFORE the row update (privacy-safe ordering, re-runnable on failure).
    assert journal.index(("delete_resume", "s3://resume-bkt/resumes/sub-1.pdf")) < journal.index(
        ("set_consent", False)
    )
    assert body["consent_store_materials"] is False


async def test_revoke_with_no_stored_resume_is_safe(monkeypatch):
    from src.api import consent

    journal = _wire(monkeypatch, resume_uri=None)
    await consent.put_consent(authorization="Bearer t",
                              consent_store_materials=False, retention_days=30)
    assert ("delete_resume", None) in journal  # no-op delete (returns 0), no exception


async def test_grant_does_not_touch_s3(monkeypatch):
    from src.api import consent

    journal = _wire(monkeypatch, resume_uri="s3://resume-bkt/resumes/sub-1.pdf")
    await consent.put_consent(authorization="Bearer t",
                              consent_store_materials=True, retention_days=30)
    assert all(call[0] != "delete_resume" for call in journal)
    assert ("set_consent", True) in journal
