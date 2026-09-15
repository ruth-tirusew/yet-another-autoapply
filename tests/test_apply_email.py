"""Regression tests for the email apply adapter honestly reporting draft-only applies.

Previously EmailApplyAdapter.apply() returned success=True with no other signal,
which the dispatcher mapped straight to status "applied" and event
"apply_succeeded" — even though nothing was sent to the employer, only a local
.txt draft was written. That made a mailto: job look like a completed
application in the queue and in application history.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.helpers import TempDBTestCase


class EmailApplyAdapterTests(TempDBTestCase):
    def test_apply_marks_manual_required_not_a_real_success(self):
        from src.apply.email import EmailApplyAdapter
        from src.settings import applications_dir

        adapter = EmailApplyAdapter()
        job = {"id": 1, "title": "Backend Engineer", "url": "mailto:jobs@example.com"}
        result = adapter.apply(job, cv_path="/tmp/does-not-matter.pdf", cover_letter="Dear hiring team,")

        self.assertTrue(result.success)
        self.assertTrue(result.manual_required)
        self.assertFalse(result.needs_verification)
        draft_path = applications_dir() / "1" / "email_draft.txt"
        self.assertTrue(draft_path.exists())


class ApplyToJobEmailStatusTests(TempDBTestCase):
    def setUp(self):
        super().setUp()
        from src.db import connect, create_user
        from src.tenant import set_tenant_user_id

        self.user_id = create_user("email-apply@test.com", display_name="Email Apply")["id"]
        set_tenant_user_id(self.user_id)

        with connect() as conn:
            conn.execute(
                """
                INSERT INTO catalog_jobs (url_hash, title, url, description_full, status)
                VALUES ('mailto-job', 'Backend Engineer', 'mailto:jobs@example.com', 'Great role.', 'active')
                """
            )
            catalog_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO user_jobs (user_id, catalog_job_id, status) VALUES (?, ?, 'queued')",
                (self.user_id, catalog_id),
            )
            self.job_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        from src.settings import applications_dir

        app_dir = applications_dir() / str(self.job_id)
        app_dir.mkdir(parents=True, exist_ok=True)
        cv_path = app_dir / "cv_tailored.pdf"
        cv_path.write_bytes(b"%PDF-1.4 fake")

        from src.db import save_application

        save_application(self.job_id, tailored_cv_path=str(cv_path), user_id=self.user_id)

    def test_email_apply_leaves_job_approved_not_applied(self):
        from src.apply.dispatcher import apply_to_job
        from src.db import count_applications_today, get_application_events, get_job

        result = apply_to_job(self.job_id, force=True)

        self.assertTrue(result["success"])
        job = get_job(self.job_id, user_id=self.user_id)
        self.assertEqual(job["status"], "approved")

        events = get_application_events(self.job_id)
        draft_events = [e for e in events if e["event_type"] == "apply_draft_saved"]
        self.assertEqual(len(draft_events), 1)
        applied_events = [e for e in events if e["event_type"] == "apply_succeeded"]
        self.assertEqual(applied_events, [])

        # A draft-only "apply" shouldn't consume the daily auto-apply cap.
        self.assertEqual(count_applications_today(self.user_id), 0)
