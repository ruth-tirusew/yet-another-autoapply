"""Shared CV HTML/PDF rendering from JSON Resume."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Template

from src.config import ROOT
from src.cv_templates.models import CvTemplate
from src.cv_templates.resolver import resolve_template_for_user
from src.cv_templates.store import load_template_source
from src.profile_format import normalize_skills
from src.tenant import resolve_user_id

TEMPLATES_DIR = ROOT / "templates"


def _normalize_resume(resume_data: dict) -> dict:
    resume = dict(resume_data)
    skills = normalize_skills(resume.get("skills"))
    if skills:
        resume["skills"] = skills
    elif "skills" in resume:
        resume["skills"] = None
    return resume


def render_resume_html(
    resume_data: dict,
    *,
    template: CvTemplate | None = None,
    user_id: int | None = None,
) -> str:
    tpl = template or resolve_template_for_user(user_id)
    html_source = load_template_source(tpl)
    resume = _normalize_resume(resume_data)
    return Template(html_source).render(resume=resume)


def render_resume_pdf(
    resume_data: dict,
    pdf_path: Path,
    *,
    template: CvTemplate | None = None,
    user_id: int | None = None,
) -> Path:
    """Render resume to PDF if WeasyPrint is available, else HTML alongside."""
    uid = resolve_user_id(user_id)
    html = render_resume_html(resume_data, template=template, user_id=uid)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from weasyprint import HTML

        HTML(string=html).write_pdf(str(pdf_path))
        return pdf_path
    except ImportError:
        html_path = pdf_path.with_suffix(".html")
        html_path.write_text(html, encoding="utf-8")
        return html_path


def _default_html_template() -> str:
    return """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
body{font-family:Arial,sans-serif;max-width:800px;margin:40px auto;line-height:1.5;color:#1e293b;background:#fff}
h1{margin-bottom:0}h2{border-bottom:1px solid #ccc;margin-top:24px}
ul{margin:4px 0;padding-left:20px}.meta{color:#475569;font-size:14px}
</style></head><body>
{% set b = resume.basics or {} %}
<h1>{{ b.name or 'Candidate' }}</h1>
<p class="meta">{{ b.email or '' }} | {{ b.phone or '' }} | {{ b.url or '' }}</p>
{% if b.summary %}<p>{{ b.summary }}</p>{% endif %}
</body></html>"""
