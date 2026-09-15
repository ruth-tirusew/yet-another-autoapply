"""CV template studio: presets, compilation, and resolution."""

from src.cv_templates.models import CvTemplate, CvTemplatePreset
from src.cv_templates.resolver import (
    resolve_template_for_job,
    resolve_template_for_user,
)
from src.cv_templates.store import (
    ensure_user_templates,
    get_default_template,
    list_templates,
)

__all__ = [
    "CvTemplate",
    "CvTemplatePreset",
    "ensure_user_templates",
    "get_default_template",
    "list_templates",
    "resolve_template_for_job",
    "resolve_template_for_user",
]
