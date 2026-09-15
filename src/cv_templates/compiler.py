"""Compile CV template presets into Jinja HTML templates."""

from __future__ import annotations

import json
from pathlib import Path

from src.config import ROOT
from src.cv_templates.models import CvTemplatePreset, SectionPreset

PARTIALS_DIR = ROOT / "templates" / "cv" / "_partials"

_SECTION_PARTIALS = {
    "basics": "basics.jinja",
    "summary": "summary.jinja",
    "work": "work.jinja",
    "projects": "projects.jinja",
    "education": "education.jinja",
}


def _load_partial(name: str) -> str:
    path = PARTIALS_DIR / name
    return path.read_text(encoding="utf-8")


def _heading_size(scale: str) -> tuple[str, str, str]:
    if scale == "compact":
        return "22px", "16px", "14px"
    if scale == "large":
        return "32px", "20px", "16px"
    return "28px", "18px", "15px"


def _build_css(preset: CvTemplatePreset) -> str:
    h1, h2, h3 = _heading_size(preset.typography.heading_scale)
    page = preset.page
    typo = preset.typography
    colors = preset.colors
    compact = preset.layout == "compact"
    line_height = "1.4" if compact else "1.5"
    section_margin = "16px" if compact else "24px"

    css = f"""
body {{
  font-family: {typo.font_family};
  max-width: {page.max_width};
  margin: {page.margin} auto;
  line-height: {line_height};
  color: {colors.text};
  background: #fff;
  font-size: {typo.body_size};
}}
h1 {{
  margin-bottom: 0;
  color: {colors.heading};
  font-size: {h1};
}}
h2 {{
  border-bottom: 1px solid {colors.border};
  margin-top: {section_margin};
  color: {colors.heading};
  font-size: {h2};
  padding-bottom: 4px;
}}
h3 {{
  color: {colors.heading};
  font-size: {h3};
  margin-bottom: 4px;
}}
ul {{
  margin: 4px 0;
  padding-left: 20px;
}}
.meta {{
  color: {typo.meta_color};
  font-size: 13px;
}}
.summary {{
  margin-top: 12px;
}}
.skill-tags {{
  line-height: 1.8;
}}
.skill-tag {{
  display: inline-block;
  background: {colors.accent}15;
  color: {colors.heading};
  border: 1px solid {colors.border};
  border-radius: 4px;
  padding: 2px 8px;
  margin: 2px 4px 2px 0;
  font-size: 12px;
}}
"""
    if preset.print_optimized:
        css += """
@media print {
  body { color: #000; background: #fff; }
  h1, h2, h3, p, li { color: #000; }
  .meta { color: #333; }
}
"""
    return css.strip()


def _render_section(section: SectionPreset, preset: CvTemplatePreset) -> str:
    if not section.visible:
        return ""

    section_id = section.id
    title = section.title or ""
    skills_style = section.style or preset.skills_style

    if section_id == "skills":
        partial_name = "skills_tags.jinja" if skills_style == "tags" else "skills_inline.jinja"
        body = _load_partial(partial_name)
    else:
        partial_file = _SECTION_PARTIALS.get(section_id)
        if not partial_file:
            return ""
        body = _load_partial(partial_file)

    if section_id in ("work", "projects", "education", "skills") and title:
        body = f"{{% set section_title = {json.dumps(title)} %}}\n" + body

    return body


def compile_preset(preset: CvTemplatePreset) -> str:
    """Turn a preset into a full Jinja HTML document."""
    sections = preset.sections or CvTemplatePreset.default_sections()
    section_html = "\n".join(_render_section(s, preset) for s in sections)
    css = _build_css(preset)
    show_profiles = "true" if preset.show_profiles else "false"

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
{css}
</style></head><body>
{{% set show_profiles = {show_profiles} %}}
{section_html}
</body></html>"""


def write_compiled_template(preset: CvTemplatePreset, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(compile_preset(preset), encoding="utf-8")
    return dest
