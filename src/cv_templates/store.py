"""CV template persistence and CRUD."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from src.cv_templates.builtins import BUILTIN_DEFINITIONS
from src.cv_templates.compiler import compile_preset, write_compiled_template
from src.cv_templates.models import CvTemplate, CvTemplatePreset, SectionPreset
from src.db import connect
from src.settings import user_data_dir
from src.tenant import resolve_user_id


def user_templates_dir(user_id: int | None = None) -> Path:
    uid = resolve_user_id(user_id)
    return user_data_dir(uid) / "templates"


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "template"


def _unique_slug(user_id: int, base: str) -> str:
    slug = base
    n = 2
    while get_template_by_slug(slug, user_id):
        slug = f"{base}-{n}"
        n += 1
    return slug


def list_templates(user_id: int | None = None) -> list[CvTemplate]:
    uid = resolve_user_id(user_id)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM cv_templates
            WHERE user_id = ?
            ORDER BY is_default DESC, kind ASC, name ASC
            """,
            (uid,),
        ).fetchall()
    return [CvTemplate.from_row(dict(r)) for r in rows]


def get_template(template_id: int, user_id: int | None = None) -> CvTemplate | None:
    uid = resolve_user_id(user_id)
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM cv_templates WHERE id = ? AND user_id = ?",
            (template_id, uid),
        ).fetchone()
    return CvTemplate.from_row(dict(row)) if row else None


def get_template_by_slug(slug: str, user_id: int | None = None) -> CvTemplate | None:
    uid = resolve_user_id(user_id)
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM cv_templates WHERE slug = ? AND user_id = ?",
            (slug, uid),
        ).fetchone()
    return CvTemplate.from_row(dict(row)) if row else None


def get_default_template(user_id: int | None = None) -> CvTemplate:
    uid = resolve_user_id(user_id)
    ensure_user_templates(uid)
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM cv_templates WHERE user_id = ? AND is_default = 1 LIMIT 1",
            (uid,),
        ).fetchone()
    if row:
        return CvTemplate.from_row(dict(row))
    templates = list_templates(uid)
    if templates:
        return templates[0]
    raise RuntimeError("No CV templates available")


def set_default_template(template_id: int, user_id: int | None = None) -> CvTemplate:
    uid = resolve_user_id(user_id)
    tpl = get_template(template_id, uid)
    if not tpl:
        raise ValueError(f"Template {template_id} not found")
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute(
            "UPDATE cv_templates SET is_default = 0, updated_at = ? WHERE user_id = ?",
            (now, uid),
        )
        conn.execute(
            "UPDATE cv_templates SET is_default = 1, updated_at = ? WHERE id = ? AND user_id = ?",
            (now, template_id, uid),
        )
    return get_template(template_id, uid)  # type: ignore[return-value]


def _template_html_path(user_id: int, slug: str) -> Path:
    return user_templates_dir(user_id) / slug / "template.html.jinja"


def load_template_source(template: CvTemplate) -> str:
    if template.kind == "custom" and template.html_path:
        path = Path(template.html_path)
        if path.exists():
            return path.read_text(encoding="utf-8")
    compiled = _template_html_path(template.user_id, template.slug)
    if compiled.exists():
        return compiled.read_text(encoding="utf-8")
    return compile_preset(template.preset)


def save_template_preset(
    slug: str,
    preset: CvTemplatePreset,
    *,
    user_id: int | None = None,
    name: str | None = None,
) -> CvTemplate:
    uid = resolve_user_id(user_id)
    tpl = get_template_by_slug(slug, uid)
    if not tpl:
        raise ValueError(f"Template {slug} not found")
    if tpl.kind == "builtin":
        raise ValueError("Built-in templates cannot be edited directly — duplicate first")

    now = datetime.now(timezone.utc).isoformat()
    html_path = _template_html_path(uid, slug)
    write_compiled_template(preset, html_path)

    with connect() as conn:
        conn.execute(
            """
            UPDATE cv_templates
            SET preset_json = ?, html_path = ?, name = COALESCE(?, name), updated_at = ?
            WHERE slug = ? AND user_id = ?
            """,
            (
                preset.model_dump_json(),
                str(html_path),
                name,
                now,
                slug,
                uid,
            ),
        )
    return get_template_by_slug(slug, uid)  # type: ignore[return-value]


def duplicate_template(
    source_slug: str,
    new_name: str,
    *,
    user_id: int | None = None,
) -> CvTemplate:
    uid = resolve_user_id(user_id)
    source = get_template_by_slug(source_slug, uid)
    if not source:
        raise ValueError(f"Template {source_slug} not found")

    base_slug = _slugify(new_name)
    slug = _unique_slug(uid, base_slug)
    now = datetime.now(timezone.utc).isoformat()
    html_path = _template_html_path(uid, slug)
    write_compiled_template(source.preset, html_path)

    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO cv_templates
                (user_id, slug, name, description, kind, preset_json, html_path, is_default, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'preset', ?, ?, 0, ?, ?)
            """,
            (
                uid,
                slug,
                new_name,
                source.description,
                source.preset.model_dump_json(),
                str(html_path),
                now,
                now,
            ),
        )
        template_id = cur.lastrowid
    return get_template(template_id, uid)  # type: ignore[return-value]


def delete_template(slug: str, *, user_id: int | None = None) -> None:
    uid = resolve_user_id(user_id)
    tpl = get_template_by_slug(slug, uid)
    if not tpl:
        raise ValueError(f"Template {slug} not found")
    if tpl.kind == "builtin":
        raise ValueError("Built-in templates cannot be deleted")
    if tpl.is_default:
        raise ValueError("Cannot delete the default template — set another default first")

    with connect() as conn:
        conn.execute(
            "DELETE FROM cv_templates WHERE slug = ? AND user_id = ?",
            (slug, uid),
        )

    tpl_dir = user_templates_dir(uid) / slug
    if tpl_dir.exists():
        import shutil

        shutil.rmtree(tpl_dir, ignore_errors=True)


def ensure_user_templates(user_id: int | None = None) -> list[CvTemplate]:
    uid = resolve_user_id(user_id)
    existing = list_templates(uid)
    if existing:
        return existing

    now = datetime.now(timezone.utc).isoformat()
    user_templates_dir(uid).mkdir(parents=True, exist_ok=True)

    created: list[CvTemplate] = []
    for i, (slug, meta) in enumerate(BUILTIN_DEFINITIONS.items()):
        preset = meta["preset"]()
        html_path = _template_html_path(uid, slug)
        write_compiled_template(preset, html_path)
        is_default = 1 if slug == "classic" else 0
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO cv_templates
                    (user_id, slug, name, description, kind, preset_json, html_path, is_default, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'builtin', ?, ?, ?, ?, ?)
                """,
                (
                    uid,
                    slug,
                    meta["name"],
                    meta["description"],
                    preset.model_dump_json(),
                    str(html_path),
                    is_default,
                    now,
                    now,
                ),
            )
        tpl = get_template_by_slug(slug, uid)
        if tpl:
            created.append(tpl)
    return created


def preset_from_form(form: dict) -> CvTemplatePreset:
    """Build a preset from HTML form fields."""
    sections_raw = form.get("sections_json") or "[]"
    try:
        sections_data = json.loads(sections_raw) if isinstance(sections_raw, str) else sections_raw
    except json.JSONDecodeError:
        sections_data = []

    sections = [SectionPreset(**s) for s in sections_data] if sections_data else CvTemplatePreset.default_sections()

    return CvTemplatePreset(
        version=1,
        page={
            "size": form.get("page_size") or "A4",
            "margin": form.get("page_margin") or "40px",
            "max_width": form.get("page_max_width") or "800px",
        },
        typography={
            "font_family": form.get("font_family") or "Arial, sans-serif",
            "heading_scale": form.get("heading_scale") or "default",
            "body_size": form.get("body_size") or "14px",
            "meta_color": form.get("meta_color") or "#475569",
        },
        colors={
            "text": form.get("color_text") or "#1e293b",
            "heading": form.get("color_heading") or "#0f172a",
            "accent": form.get("color_accent") or "#2563eb",
            "border": form.get("color_border") or "#cbd5e1",
        },
        layout=form.get("layout") or "single_column",
        sections=sections,
        skills_style=form.get("skills_style") or "inline",
        show_profiles=form.get("show_profiles") in ("on", "true", "1", True),
        print_optimized=form.get("print_optimized") in ("on", "true", "1", True, None),
    )
