"""Email / mailto apply fallback."""

from __future__ import annotations

from typing import Any

from src.apply.base import ApplyResult, BaseApplyAdapter
from src.config import get_config
from src.settings import applications_dir


class EmailApplyAdapter(BaseApplyAdapter):
    def can_apply(self, url: str) -> bool:
        return url.lower().startswith("mailto:")

    def apply(self, job: dict[str, Any], cv_path: str, cover_letter: str) -> ApplyResult:
        cfg = get_config()
        applicant = cfg.get("applicant", {})
        url = job.get("url", "")

        out_dir = applications_dir() / str(job["id"])
        out_dir.mkdir(parents=True, exist_ok=True)
        draft_path = out_dir / "email_draft.txt"

        body = cover_letter or f"Application for {job.get('title', '')}"
        draft = f"To: {url.replace('mailto:', '')}\nFrom: {applicant.get('email', '')}\nSubject: Application — {job.get('title', '')}\n\n{body}\n\nCV attached: {cv_path}"
        draft_path.write_text(draft, encoding="utf-8")

        return ApplyResult(True, "email", f"Email draft saved to {draft_path}", None, manual_required=True)
