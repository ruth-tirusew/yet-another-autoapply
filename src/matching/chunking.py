"""Split resumes and job postings into retrievable units.

The old pipeline embedded a whole resume (~9.5k chars) and a whole posting
(~8k chars) into one vector each, which averages away everything that
distinguishes one job from another. Here both sides are cut into small,
self-contained pieces so similarity is computed between things that are
actually comparable: one requirement against one piece of experience.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from src.matching.models import ResumeChunk
from src.profile_format import normalize_skills

CHUNK_CHARS = 1200
SKILLS_PER_CHUNK = 10

# Headings that introduce the part of a posting that states requirements.
_REQ_HEADINGS = re.compile(
    r"^\s*(?:#+\s*)?(?:what\s+(?:you'?ll|you\s+will)\s+need|what\s+we'?re\s+looking\s+for|"
    r"requirements?|qualifications?|must[-\s]?haves?|nice[-\s]?to[-\s]?haves?|"
    r"skills?\s*(?:&|and)?\s*experience|who\s+you\s+are|about\s+you|your\s+profile|"
    r"basic\s+qualifications?|preferred\s+qualifications?|minimum\s+qualifications?)"
    r"\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Headings that mark the end of useful content (boilerplate tail).
_TAIL_HEADINGS = re.compile(
    r"^\s*(?:#+\s*)?(?:benefits?|perks?|what\s+we\s+offer|compensation|salary|"
    r"equal\s+opportunity|eeo|diversity|about\s+(?:us|the\s+company)|our\s+values|"
    r"how\s+to\s+apply|application\s+process)\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Many ATS postings (Greenhouse, Lever, Ashby...) have no headings at all —
# every sentence sits on its own line with no markup — so `_TAIL_HEADINGS`
# never fires and `requirement_section` falls back to the whole description,
# benefits/EEO/application-form boilerplate included. These phrases mark the
# same kind of tail content but can appear mid-sentence rather than on their
# own heading line, so they're matched anywhere rather than anchored to a line.
_BOILERPLATE_TAIL = re.compile(
    r"(?:base\s+pay\s+is\s+just|total\s+rewards\s+program|"
    r"equal\s+opportunity\s+employer|create\s+a\s+job\s+alert|"
    r"apply\s+for\s+this\s+job|indicates?\s+a\s+required\s+field|"
    r"accepted\s+file\s+types|autofill\s+my\s+application|"
    r"talent\s+matching\s+tool|hiring\s+salary\s+range)",
    re.IGNORECASE,
)

_BULLET = re.compile(r"^\s*(?:[-*•·–—▪◦]|\d+[.)])\s+")
_NICE_MARKER = re.compile(
    r"\b(?:nice[-\s]?to[-\s]?have|preferred|bonus|plus|desirable|a\s+plus|"
    r"advantageous|ideally)\b",
    re.IGNORECASE,
)


def text_hash(text: str) -> str:
    """Stable fingerprint of a chunk's text, used to invalidate cached vectors."""
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()[:32]


def _clip(text: str, limit: int = CHUNK_CHARS) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _entry_text(entry: dict[str, Any], *, name_keys: tuple[str, ...]) -> str:
    parts: list[str] = []
    for key in name_keys:
        value = entry.get(key)
        if value:
            parts.append(str(value))
    header = " — ".join(parts)
    body = [str(entry.get("summary") or entry.get("description") or "")]
    for highlight in entry.get("highlights") or []:
        body.append(f"- {highlight}")
    for keyword in entry.get("keywords") or []:
        body.append(str(keyword))
    return _clip("\n".join([header, *body]))


def chunk_resume(resume: dict[str, Any] | None) -> list[ResumeChunk]:
    """Cut a JSON Resume into retrievable chunks, one per experience/project/skill group."""
    if not resume:
        return []
    chunks: list[ResumeChunk] = []

    basics = resume.get("basics") or {}
    summary = basics.get("summary") or ""
    if summary:
        chunks.append(
            ResumeChunk(id="summary", kind="summary", ref="Profile summary", text=_clip(summary))
        )

    for index, entry in enumerate(resume.get("work") or []):
        if not isinstance(entry, dict):
            continue
        text = _entry_text(entry, name_keys=("position", "name", "company"))
        if not text:
            continue
        label = " @ ".join(
            p for p in [entry.get("position") or "", entry.get("name") or entry.get("company") or ""] if p
        )
        period = " ".join(p for p in [entry.get("startDate") or "", entry.get("endDate") or ""] if p)
        chunks.append(
            ResumeChunk(
                id=f"work-{index}",
                kind="work",
                ref=f"{label} ({period})".strip() or f"work[{index}]",
                text=text,
            )
        )

    for index, entry in enumerate(resume.get("projects") or []):
        if not isinstance(entry, dict):
            continue
        text = _entry_text(entry, name_keys=("name", "entity"))
        if not text:
            continue
        chunks.append(
            ResumeChunk(
                id=f"project-{index}",
                kind="project",
                ref=str(entry.get("name") or f"project[{index}]"),
                text=text,
            )
        )

    skills = normalize_skills(resume.get("skills"))
    flat: list[str] = []
    for skill in skills:
        name = str(skill.get("name") or "").strip()
        keywords = [str(k).strip() for k in (skill.get("keywords") or []) if str(k).strip()]
        if name and keywords:
            flat.append(f"{name}: {', '.join(keywords)}")
        elif name:
            flat.append(name)
        else:
            flat.extend(keywords)
    for index in range(0, len(flat), SKILLS_PER_CHUNK):
        group = flat[index : index + SKILLS_PER_CHUNK]
        chunks.append(
            ResumeChunk(
                id=f"skills-{index // SKILLS_PER_CHUNK}",
                kind="skills",
                ref="Skills",
                text=_clip("; ".join(group)),
            )
        )

    for index, entry in enumerate(resume.get("education") or []):
        if not isinstance(entry, dict):
            continue
        label = " ".join(
            str(entry.get(k) or "")
            for k in ("studyType", "area", "institution")
        ).strip()
        if not label:
            continue
        chunks.append(
            ResumeChunk(
                id=f"education-{index}",
                kind="education",
                ref=label,
                text=_clip(f"{label} {entry.get('startDate') or ''} {entry.get('endDate') or ''}"),
            )
        )

    return chunks


def requirement_section(description: str) -> str:
    """Return the slice of a posting that states requirements.

    Postings routinely bury requirements under company boilerplate and close
    with benefits/EEO text, so a blind head-truncation often keeps the
    marketing copy and drops the part that matters. When no requirements
    heading is found the whole description is returned unchanged.
    """
    text = description or ""
    if not text.strip():
        return ""
    start_match = _REQ_HEADINGS.search(text)
    start = start_match.start() if start_match else 0
    search_from = start + 1 if start_match else 0
    tail_match = _TAIL_HEADINGS.search(text, search_from)
    boilerplate_match = _BOILERPLATE_TAIL.search(text, search_from)
    ends = [m.start() for m in (tail_match, boilerplate_match) if m]
    end = min(ends) if ends else len(text)
    section = text[start:end].strip()
    return section or text.strip()


def heuristic_requirements(description: str, *, limit: int = 20) -> list[tuple[str, str]]:
    """Bullet-level requirement candidates without an LLM.

    Returns ``(text, kind)`` pairs. Used as a fallback when no model is
    reachable, and as unit-testable ground truth for the extraction contract.
    """
    section = requirement_section(description)
    if not section:
        return []

    out: list[tuple[str, str]] = []
    nice_context = False
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _REQ_HEADINGS.match(line) or line.endswith(":"):
            nice_context = bool(_NICE_MARKER.search(line))
            if not _BULLET.match(line):
                continue
        if not _BULLET.match(line):
            continue
        cleaned = _BULLET.sub("", line).strip(" .;")
        if len(cleaned) < 2:
            continue
        kind = "nice" if (nice_context or _NICE_MARKER.search(cleaned)) else "must"
        out.append((cleaned, kind))
        if len(out) >= limit:
            break
    return out


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_MIN_SENTENCE_CHARS = 20


def chunk_posting(description: str, *, max_chunks: int = 12, max_chars: int = 300) -> list[str]:
    """Cheap, LLM-free split of a posting into short spans for the vector prefilter.

    The prefilter has to run before requirement extraction (it exists to
    gate the expensive LLM stages, extraction included), so it cannot use
    the LLM-derived :class:`~src.matching.models.Requirement` objects.
    Instead this reuses the same bullet-oriented heuristic as
    :func:`heuristic_requirements` but returns plain text with no must/nice
    classification, and falls back to sentence-splitting the requirements
    section when the posting has no bullets, so prose-only postings still
    produce comparable chunks instead of an empty list.
    """
    section = requirement_section(description)
    if not section:
        return []

    bullets: list[str] = []
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line or not _BULLET.match(line):
            continue
        cleaned = _BULLET.sub("", line).strip(" .;")
        if len(cleaned) >= 2:
            bullets.append(_clip(cleaned, max_chars))
        if len(bullets) >= max_chunks:
            break
    if bullets:
        return bullets

    sentences = [
        _clip(s.strip(), max_chars)
        for s in _SENTENCE_SPLIT_RE.split(section)
        if len(s.strip()) >= _MIN_SENTENCE_CHARS
    ]
    return sentences[:max_chunks]
