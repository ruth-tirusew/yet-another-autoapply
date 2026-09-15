"""Authentication and multi-tenant isolation tests."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def auth_client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("SESSION_SECRET", "test-secret-key-for-tests-only")
    monkeypatch.setattr("src.settings.DB_PATH", db_path)
    monkeypatch.setattr("src.settings.DATA_DIR", tmp_path)
    monkeypatch.setattr("src.settings.PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr("src.settings.ROOT", Path(__file__).resolve().parent.parent)

    from src.db import init_db
    from src.web.app import app

    init_db()
    with TestClient(app) as client:
        yield client


def test_register_login_logout_flow(auth_client):
    reg = auth_client.post(
        "/register",
        data={
            "email": "alice@example.com",
            "password": "password123",
            "password_confirm": "password123",
            "display_name": "Alice",
            "csrf_token": _csrf(auth_client, "/register"),
        },
        follow_redirects=False,
    )
    assert reg.status_code == 303
    assert reg.headers["location"] == "/profile"

    auth_client.post("/logout", follow_redirects=False)
    denied = auth_client.get("/", follow_redirects=False)
    assert denied.status_code == 303
    assert denied.headers["location"].startswith("/login")

    login = auth_client.post(
        "/login",
        data={
            "email": "alice@example.com",
            "password": "password123",
            "csrf_token": _csrf(auth_client, "/login"),
        },
        follow_redirects=False,
    )
    assert login.status_code == 303

    home = auth_client.get("/")
    assert home.status_code == 200


def test_duplicate_email_rejected(auth_client):
    data = {
        "email": "bob@example.com",
        "password": "password123",
        "password_confirm": "password123",
        "display_name": "Bob",
        "csrf_token": _csrf(auth_client, "/register"),
    }
    assert auth_client.post("/register", data=data, follow_redirects=False).status_code == 303
    auth_client.post("/logout")
    dup = auth_client.post("/register", data=data, follow_redirects=False)
    assert dup.status_code == 400


def test_tenant_isolation(auth_client):
    auth_client.post(
        "/register",
        data={
            "email": "usera@test.com",
            "password": "password123",
            "password_confirm": "password123",
            "display_name": "User A",
            "csrf_token": _csrf(auth_client, "/register"),
        },
        follow_redirects=False,
    )

    from src.db import upsert_job, get_job
    from src.tenant import set_tenant_user_id

    # current_user_id is a contextvar set directly (not via a request thread),
    # so it persists in this test's context for the rest of the pytest process
    # unless explicitly reset — reset it in `finally` so it can't leak into
    # later tests that rely on the tenant-context fallback default.
    try:
        set_tenant_user_id(1)
        job_id = upsert_job({"url": "https://example.com/jobs/1", "title": "Job A"}, user_id=1)
        assert job_id > 0

        auth_client.post("/logout")
        auth_client.post(
            "/register",
            data={
                "email": "userb@test.com",
                "password": "password123",
                "password_confirm": "password123",
                "display_name": "User B",
                "csrf_token": _csrf(auth_client, "/register"),
            },
            follow_redirects=False,
        )

        set_tenant_user_id(2)
        assert get_job(job_id, user_id=2) is None
        assert get_job(job_id, user_id=1) is not None
    finally:
        set_tenant_user_id(None)


def test_migration_creates_bootstrap_user(tmp_path, monkeypatch):
    db_path = tmp_path / "migrate.db"
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setattr("src.settings.DB_PATH", db_path)
    monkeypatch.setattr("src.settings.DATA_DIR", tmp_path)
    monkeypatch.setattr("src.settings.PROFILE_DIR", tmp_path / "profile")

    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url_hash TEXT UNIQUE NOT NULL,
            url TEXT,
            status TEXT DEFAULT 'new',
            first_seen TEXT,
            last_seen TEXT
        );
        INSERT INTO jobs (url_hash, url, first_seen, last_seen)
        VALUES ('abc', 'https://legacy.example/job', '2020-01-01', '2020-01-01');
        """
    )
    conn.commit()
    conn.close()

    config_path = tmp_path / "config.yaml"
    config_path.write_text("applicant:\n  email: legacy@example.com\n  name: Legacy User\n")
    monkeypatch.setattr("src.settings.ROOT", tmp_path)

    import importlib

    import src.db as dbmod

    importlib.reload(dbmod)
    dbmod.init_db()
    user = dbmod.get_user_by_email("legacy@example.com")
    assert user is not None
    assert user["id"] == 1


def _csrf(client: TestClient, path: str) -> str:
    resp = client.get(path)
    start = resp.text.find('name="csrf_token" value="')
    assert start != -1
    start += len('name="csrf_token" value="')
    end = resp.text.find('"', start)
    return resp.text[start:end]


def test_password_reset_flow(auth_client, monkeypatch):
    monkeypatch.setattr("src.settings.PASSWORD_RESET_DEV", True)
    monkeypatch.setattr("src.settings.smtp_configured", lambda: False)

    auth_client.post(
        "/register",
        data={
            "email": "resetme@example.com",
            "password": "oldpassword1",
            "password_confirm": "oldpassword1",
            "display_name": "Reset Me",
            "csrf_token": _csrf(auth_client, "/register"),
        },
        follow_redirects=False,
    )
    auth_client.post("/logout")

    forgot = auth_client.post(
        "/forgot-password",
        data={
            "email": "resetme@example.com",
            "csrf_token": _csrf(auth_client, "/forgot-password"),
        },
    )
    assert forgot.status_code == 200
    assert "Dev reset link" in forgot.text
    start = forgot.text.find("/reset-password?token=")
    assert start != -1
    end = forgot.text.find('"', start)
    reset_path = forgot.text[start:end]

    reset_page = auth_client.get(reset_path)
    assert reset_page.status_code == 200

    token = reset_path.split("token=", 1)[1]
    done = auth_client.post(
        "/reset-password",
        data={
            "token": token,
            "password": "newpassword1",
            "password_confirm": "newpassword1",
            "csrf_token": _csrf(auth_client, reset_path),
        },
        follow_redirects=False,
    )
    assert done.status_code == 303
    assert done.headers["location"] == "/login?reset=ok"

    login = auth_client.post(
        "/login",
        data={
            "email": "resetme@example.com",
            "password": "newpassword1",
            "csrf_token": _csrf(auth_client, "/login"),
        },
        follow_redirects=False,
    )
    assert login.status_code == 303
    assert auth_client.get("/").status_code == 200
