"""Resolve which CV template to use for rendering."""

from __future__ import annotations

from src.cv_templates.models import CvTemplate
from src.cv_templates.store import ensure_user_templates, get_default_template, get_template
from src.db import get_application
from src.tenant import resolve_user_id


def resolve_template_for_user(user_id: int | None = None) -> CvTemplate:
    uid = resolve_user_id(user_id)
    ensure_user_templates(uid)
    return get_default_template(uid)


def resolve_template_for_job(job_id: int, user_id: int | None = None) -> CvTemplate:
    uid = resolve_user_id(user_id)
    ensure_user_templates(uid)
    app = get_application(job_id, uid)
    if app and app.get("cv_template_id"):
        tpl = get_template(app["cv_template_id"], uid)
        if tpl:
            return tpl
    return get_default_template(uid)


def resolve_template_by_id(template_id: int | None, user_id: int | None = None) -> CvTemplate:
    uid = resolve_user_id(user_id)
    ensure_user_templates(uid)
    if template_id:
        tpl = get_template(template_id, uid)
        if tpl:
            return tpl
    return get_default_template(uid)
