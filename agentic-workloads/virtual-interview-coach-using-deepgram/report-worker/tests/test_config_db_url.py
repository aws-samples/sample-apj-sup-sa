"""Config DB-URL assembly: DB_SECRET_ARN flips the DSN to passwordless (the live password then
arrives via the provider callable), while the static DB_PASSWORD path is preserved for local/dev."""

from __future__ import annotations

from src import config


def _clear(monkeypatch):
    for k in ("DATABASE_URL", "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_SECRET_ARN"):
        monkeypatch.delenv(k, raising=False)


def test_secret_arn_yields_passwordless_dsn(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("DB_HOST", "db.example.com")
    monkeypatch.setenv("DB_SECRET_ARN", "arn:secret")
    monkeypatch.setenv("DB_PASSWORD", "ignored-when-secret-set")
    url = config._database_url()
    assert url == "postgres://icadmin@db.example.com:5432/interviewcoach"
    assert "ignored-when-secret-set" not in (url or "")


def test_static_password_path_preserved(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("DB_HOST", "db.example.com")
    monkeypatch.setenv("DB_PASSWORD", "p@ss/word")  # reserved chars must be URL-encoded
    url = config._database_url()
    assert url == "postgres://icadmin:p%40ss%2Fword@db.example.com:5432/interviewcoach"


def test_explicit_database_url_wins(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h:5432/d")
    monkeypatch.setenv("DB_SECRET_ARN", "arn:secret")
    assert config._database_url() == "postgres://u:p@h:5432/d"


def test_no_host_no_url(monkeypatch):
    _clear(monkeypatch)
    assert config._database_url() is None
