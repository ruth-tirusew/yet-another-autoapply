"""Builtin CV template presets."""

from __future__ import annotations

from src.cv_templates.models import (
    ColorsPreset,
    CvTemplatePreset,
    PagePreset,
    SectionPreset,
    TypographyPreset,
)


def _base_sections() -> list[SectionPreset]:
    return CvTemplatePreset.default_sections()


def classic_preset() -> CvTemplatePreset:
    return CvTemplatePreset(
        version=1,
        page=PagePreset(size="A4", margin="40px", max_width="800px"),
        typography=TypographyPreset(
            font_family="Arial, sans-serif",
            heading_scale="default",
            body_size="14px",
            meta_color="#475569",
        ),
        colors=ColorsPreset(
            text="#1e293b",
            heading="#0f172a",
            accent="#2563eb",
            border="#cbd5e1",
        ),
        layout="single_column",
        sections=_base_sections(),
        skills_style="inline",
        show_profiles=True,
        print_optimized=True,
    )


def compact_preset() -> CvTemplatePreset:
    preset = classic_preset()
    preset.layout = "compact"
    preset.page = PagePreset(size="A4", margin="24px", max_width="720px")
    preset.typography = TypographyPreset(
        font_family="Helvetica, Arial, sans-serif",
        heading_scale="compact",
        body_size="13px",
        meta_color="#64748b",
    )
    return preset


def modern_preset() -> CvTemplatePreset:
    preset = classic_preset()
    preset.typography = TypographyPreset(
        font_family="Georgia, 'Times New Roman', serif",
        heading_scale="large",
        body_size="14px",
        meta_color="#475569",
    )
    preset.colors = ColorsPreset(
        text="#334155",
        heading="#1e3a5f",
        accent="#4859FF",
        border="#94a3b8",
    )
    preset.skills_style = "tags"
    sections = _base_sections()
    for s in sections:
        if s.id == "skills":
            s.style = "tags"
    preset.sections = sections
    return preset


BUILTIN_DEFINITIONS: dict[str, dict] = {
    "classic": {
        "name": "Classic",
        "description": "Clean single-column layout matching the original resume style.",
        "preset": classic_preset,
    },
    "compact": {
        "name": "Compact",
        "description": "Tighter spacing and smaller type for dense experience lists.",
        "preset": compact_preset,
    },
    "modern": {
        "name": "Modern",
        "description": "Serif headings with skill tags and accent styling.",
        "preset": modern_preset,
    },
}
