"""Knowledge base: Markdown source files -> database -> context for the AI services.

* ``knowledge/*.md`` are the owner-maintained, version-controlled sources.
* ``sync_knowledge()`` imports them into ContentKnowledge / WritingExample. A fact is
  verified only if it carries ``(source: ...)``; anything else stays unverified and unused.
* ``load_knowledge_context()`` is what AI services call. It reads only active, verified
  knowledge and active, owner-approved examples from the database, so admin edits apply too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.db import transaction

from .models import ContentKnowledge, KnowledgeCategory, WritingExample

FACT_FILES = {
    KnowledgeCategory.FOUNDER: "founder.md",
    KnowledgeCategory.COMPANY: "company.md",
}
STYLE_FILE = "writing_style.md"
EXAMPLES_DIR = "writing_examples"
FACTS_HEADING = "facts"
SUBJECT_AREAS_HEADING = "approved subject areas"

_FACT_WITH_SOURCE = re.compile(r"^(?P<text>.+?)\s*\(source:\s*(?P<source>[^)]+)\)\s*\.?$", re.I)
_COMMENT = re.compile(r"<!--.*?-->", re.S)


# --- Markdown parsing ---------------------------------------------------------------


@dataclass(frozen=True)
class ParsedFact:
    text: str
    source: str | None  # None means unverified


def _sections(markdown: str) -> dict[str, tuple[str, str]]:
    """Split by '## ' headings into {lower-cased heading: (heading, body)}.

    Text before the first heading (title and instructions for the owner) is under ''.
    """
    sections: dict[str, tuple[str, list[str]]] = {"": ("", [])}
    current = ""
    for line in _COMMENT.sub("", markdown).splitlines():
        if line.startswith("## "):
            heading = line[3:].strip()
            current = heading.lower()
            sections[current] = (heading, [])
        else:
            sections[current][1].append(line)
    return {key: (heading, "\n".join(lines).strip()) for key, (heading, lines) in sections.items()}


def _bullets(text: str) -> list[str]:
    return [
        line[2:].strip() for line in text.splitlines() if line.startswith("- ") and line[2:].strip()
    ]


def parse_facts(markdown: str) -> list[ParsedFact]:
    facts = []
    for bullet in _bullets(_sections(markdown).get(FACTS_HEADING, ("", ""))[1]):
        match = _FACT_WITH_SOURCE.match(bullet)
        if match:
            facts.append(ParsedFact(match["text"].strip(), match["source"].strip()))
        else:
            facts.append(ParsedFact(bullet, None))
    return facts


def parse_style(markdown: str) -> tuple[str, list[str]]:
    """Return (writing rules text, approved subject areas)."""
    sections = _sections(markdown)
    subject_areas = _bullets(sections.pop(SUBJECT_AREAS_HEADING, ("", ""))[1])
    sections.pop("", None)  # owner-facing preamble, not a rule
    rules = "\n\n".join(f"## {heading}\n{body}" for heading, body in sections.values() if body)
    return rules, subject_areas


def parse_example(markdown: str) -> tuple[dict[str, str], str]:
    """Split optional '---' front matter (key: value lines) from the post body."""
    meta: dict[str, str] = {}
    body = markdown
    if markdown.startswith("---"):
        parts = markdown.split("---", 2)
        if len(parts) == 3:
            for line in parts[1].splitlines():
                key, sep, value = line.partition(":")
                if sep:
                    meta[key.strip().lower()] = value.strip()
            body = parts[2]
    return meta, body.strip()


# --- Sync to database -----------------------------------------------------------------


@dataclass
class SyncReport:
    verified_facts: int = 0
    unverified_facts: int = 0
    deactivated: int = 0
    subject_areas: int = 0
    approved_examples: int = 0
    unapproved_examples: int = 0
    warnings: list[str] = field(default_factory=list)


def _knowledge_dir(base_dir: Path | None) -> Path:
    return Path(base_dir or settings.KNOWLEDGE_DIR)


def _upsert_knowledge(
    category: str, content: str, source: str, *, verified: bool, title: str | None = None
) -> ContentKnowledge:
    record = ContentKnowledge.objects.filter(category=category, content=content).first()
    record = record or ContentKnowledge(category=category, content=content)
    record.title = (title or content)[:255]
    record.source = source[:500]
    record.is_verified = verified
    record.is_active = True
    record.save()
    return record


def _deactivate_missing(category: str, source_prefix: str, keep: list[int]) -> int:
    return (
        ContentKnowledge.objects.filter(
            category=category, source__startswith=source_prefix, is_active=True
        )
        .exclude(pk__in=keep)
        .update(is_active=False)
    )


@transaction.atomic
def sync_knowledge(base_dir: Path | None = None) -> SyncReport:
    root = _knowledge_dir(base_dir)
    report = SyncReport()

    for category, filename in FACT_FILES.items():
        path = root / filename
        prefix = f"knowledge/{filename}"
        if not path.exists():
            report.warnings.append(f"{prefix} not found; no {category} facts loaded.")
        facts = parse_facts(path.read_text(encoding="utf-8")) if path.exists() else []
        kept = []
        for fact in facts:
            verified = fact.source is not None
            source = f"{prefix} ({fact.source})" if verified else prefix
            kept.append(_upsert_knowledge(category, fact.text, source, verified=verified).pk)
            if verified:
                report.verified_facts += 1
            else:
                report.unverified_facts += 1
                report.warnings.append(f"Unverified (no source) in {prefix}: {fact.text[:80]}")
        report.deactivated += _deactivate_missing(category, prefix, kept)

    style_path = root / STYLE_FILE
    style_prefix = f"knowledge/{STYLE_FILE}"
    rules: str = ""
    subject_areas: list[str] = []
    if style_path.exists():
        rules, subject_areas = parse_style(style_path.read_text(encoding="utf-8"))
    else:
        report.warnings.append(f"{style_prefix} not found; no writing rules loaded.")

    kept = []
    if rules:
        kept.append(
            _upsert_knowledge(
                KnowledgeCategory.WRITING_STYLE,
                rules,
                style_prefix,
                verified=True,
                title="Writing style guide",
            ).pk
        )
    report.deactivated += _deactivate_missing(KnowledgeCategory.WRITING_STYLE, style_prefix, kept)

    kept = [
        _upsert_knowledge(KnowledgeCategory.SUBJECT_AREA, area, style_prefix, verified=True).pk
        for area in subject_areas
    ]
    report.subject_areas = len(kept)
    report.deactivated += _deactivate_missing(KnowledgeCategory.SUBJECT_AREA, style_prefix, kept)

    examples_dir = root / EXAMPLES_DIR
    for path in sorted(examples_dir.glob("*.md")) if examples_dir.is_dir() else []:
        if path.name.lower() == "readme.md":
            continue
        meta, body = parse_example(path.read_text(encoding="utf-8"))
        if not body:
            report.warnings.append(f"Skipped empty example {path.name}.")
            continue
        approved = meta.get("approved", "").lower() in {"true", "yes"}
        WritingExample.objects.update_or_create(
            title=(meta.get("title") or path.stem)[:255],
            defaults={
                "content": body,
                "category": meta.get("category", "")[:100],
                "is_owner_approved": approved,
                "is_active": True,
            },
        )
        if approved:
            report.approved_examples += 1
        else:
            report.unapproved_examples += 1
    return report


# --- Loader for AI services -------------------------------------------------------------


@dataclass(frozen=True)
class WritingSample:
    title: str
    content: str


@dataclass(frozen=True)
class KnowledgeContext:
    founder_facts: tuple[str, ...]
    company_facts: tuple[str, ...]
    subject_areas: tuple[str, ...]
    writing_rules: str
    writing_examples: tuple[WritingSample, ...]

    @property
    def has_verified_facts(self) -> bool:
        return bool(self.founder_facts or self.company_facts)


def load_knowledge_context(*, max_examples: int = 5) -> KnowledgeContext:
    usable = ContentKnowledge.objects.usable()

    def contents(category: str) -> tuple[str, ...]:
        category_rows = usable.filter(category=category).order_by("pk")
        # The owner dashboard is an explicit database override for its category. If the
        # owner clears that section, fall back to the Markdown-synced knowledge records.
        overrides = category_rows.filter(source__startswith="owner-dashboard:")
        if overrides.exists():
            category_rows = overrides
        return tuple(category_rows.values_list("content", flat=True))

    examples = WritingExample.objects.usable().order_by("-updated_at")[:max_examples]
    return KnowledgeContext(
        founder_facts=contents(KnowledgeCategory.FOUNDER),
        company_facts=contents(KnowledgeCategory.COMPANY),
        subject_areas=contents(KnowledgeCategory.SUBJECT_AREA),
        writing_rules="\n\n".join(contents(KnowledgeCategory.WRITING_STYLE)),
        writing_examples=tuple(WritingSample(e.title, e.content) for e in examples),
    )
