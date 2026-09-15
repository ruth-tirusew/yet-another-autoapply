"""Pydantic models for CV template presets."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class PagePreset(BaseModel):
    size: str = "A4"
    margin: str = "40px"
    max_width: str = "800px"


class TypographyPreset(BaseModel):
    font_family: str = "Arial, sans-serif"
    heading_scale: Literal["default", "compact", "large"] = "default"
    body_size: str = "14px"
    meta_color: str = "#475569"


class ColorsPreset(BaseModel):
    text: str = "#1e293b"
    heading: str = "#0f172a"
    accent: str = "#2563eb"
    border: str = "#cbd5e1"


class SectionPreset(BaseModel):
    id: str
    visible: bool = True
    title: str | None = None
    style: str | None = None


class CvTemplatePreset(BaseModel):
    version: int = 1
    page: PagePreset = Field(default_factory=PagePreset)
    typography: TypographyPreset = Field(default_factory=TypographyPreset)
    colors: ColorsPreset = Field(default_factory=ColorsPreset)
    layout: Literal["single_column", "compact"] = "single_column"
    sections: list[SectionPreset] = Field(default_factory=list)
    skills_style: Literal["inline", "tags"] = "inline"
    show_profiles: bool = True
    print_optimized: bool = True

    @classmethod
    def default_sections(cls) -> list[SectionPreset]:
        return [
            SectionPreset(id="basics", visible=True),
            SectionPreset(id="summary", visible=True),
            SectionPreset(id="work", visible=True, title="Experience"),
            SectionPreset(id="projects", visible=True, title="Projects"),
            SectionPreset(id="education", visible=True, title="Education"),
            SectionPreset(id="skills", visible=True, title="Skills", style="inline"),
        ]


class CvTemplate(BaseModel):
    id: int
    user_id: int
    slug: str
    name: str
    description: str = ""
    kind: Literal["builtin", "preset", "custom"] = "preset"
    preset: CvTemplatePreset
    html_path: str | None = None
    is_default: bool = False
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> CvTemplate:
        import json

        preset_raw = row.get("preset_json") or "{}"
        try:
            preset_data = json.loads(preset_raw) if isinstance(preset_raw, str) else preset_raw
        except json.JSONDecodeError:
            preset_data = {}
        if not preset_data.get("sections"):
            preset_data["sections"] = [s.model_dump() for s in CvTemplatePreset.default_sections()]
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            slug=row["slug"],
            name=row["name"],
            description=row.get("description") or "",
            kind=row.get("kind") or "preset",
            preset=CvTemplatePreset(**preset_data),
            html_path=row.get("html_path"),
            is_default=bool(row.get("is_default")),
            created_at=row.get("created_at") or "",
            updated_at=row.get("updated_at") or "",
        )
