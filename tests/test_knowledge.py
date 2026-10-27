"""Knowledge base loader and sync (brief Milestone 2)."""

from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command

from apps.content.knowledge import (
    load_knowledge_context,
    parse_example,
    parse_facts,
    parse_style,
    sync_knowledge,
)
from apps.content.models import ContentKnowledge, WritingExample

FOUNDER_MD = """# Founder

- This instruction bullet is outside the Facts section. (source: ignored)

## Facts

<!-- - commented out fact (source: nope) -->
- Has led software teams since 2015. (source: owner statement, 2026-10-14)
- Built a unicorn in a weekend.
"""

STYLE_MD = """# Writing style

Owner instructions that are not rules.

## Voice

- Short paragraphs.
- Avoid corporate buzzwords.

## Standard LinkedIn post format

1. Hook first.

## Approved subject areas

- AI
- Ruby on Rails
"""


@pytest.fixture
def knowledge_dir(tmp_path: Path) -> Path:
    (tmp_path / "founder.md").write_text(FOUNDER_MD, encoding="utf-8")
    (tmp_path / "company.md").write_text("# Company\n\n## Facts\n", encoding="utf-8")
    (tmp_path / "writing_style.md").write_text(STYLE_MD, encoding="utf-8")
    examples = tmp_path / "writing_examples"
    examples.mkdir()
    (examples / "README.md").write_text("instructions", encoding="utf-8")
    (examples / "real.md").write_text(
        "---\ntitle: Real post\ncategory: engineering\napproved: true\n---\nShip small.\n",
        encoding="utf-8",
    )
    (examples / "draft.md").write_text(
        "---\ntitle: Not approved\napproved: false\n---\nAI-ish text.\n", encoding="utf-8"
    )
    return tmp_path


def test_parse_facts_requires_source_for_verification():
    facts = parse_facts(FOUNDER_MD)
    assert [(f.text, f.source) for f in facts] == [
        ("Has led software teams since 2015.", "owner statement, 2026-10-14"),
        ("Built a unicorn in a weekend.", None),
    ]


def test_parse_style_separates_rules_and_subject_areas():
    rules, areas = parse_style(STYLE_MD)
    assert areas == ["AI", "Ruby on Rails"]
    assert "Short paragraphs." in rules
    assert "## Standard LinkedIn post format" in rules
    assert "Owner instructions" not in rules
    assert "Ruby on Rails" not in rules


def test_parse_example_front_matter():
    meta, body = parse_example("---\ntitle: T\napproved: true\n---\n\nBody text\n")
    assert meta == {"title": "T", "approved": "true"}
    assert body == "Body text"
    assert parse_example("No front matter") == ({}, "No front matter")


@pytest.mark.django_db
def test_loader_returns_only_verified_facts_style_and_approved_examples(knowledge_dir):
    report = sync_knowledge(knowledge_dir)
    assert (report.verified_facts, report.unverified_facts) == (1, 1)
    assert report.subject_areas == 2

    context = load_knowledge_context()
    assert context.founder_facts == ("Has led software teams since 2015.",)
    assert context.company_facts == ()
    assert "Built a unicorn" not in " ".join(context.founder_facts)
    assert context.subject_areas == ("AI", "Ruby on Rails")
    assert "Avoid corporate buzzwords." in context.writing_rules
    assert [e.title for e in context.writing_examples] == ["Real post"]
    assert context.has_verified_facts


@pytest.mark.django_db
def test_unsourced_claim_is_stored_unverified_with_provenance(knowledge_dir):
    sync_knowledge(knowledge_dir)
    claim = ContentKnowledge.objects.get(content="Built a unicorn in a weekend.")
    assert not claim.is_verified
    assert claim.source == "knowledge/founder.md"
    assert claim not in ContentKnowledge.objects.usable()

    fact = ContentKnowledge.objects.get(content="Has led software teams since 2015.")
    assert fact.source == "knowledge/founder.md (owner statement, 2026-10-14)"


@pytest.mark.django_db
def test_sync_is_idempotent_and_deactivates_removed_facts(knowledge_dir):
    sync_knowledge(knowledge_dir)
    sync_knowledge(knowledge_dir)
    assert ContentKnowledge.objects.filter(category="founder").count() == 2
    assert WritingExample.objects.count() == 2

    (knowledge_dir / "founder.md").write_text("## Facts\n", encoding="utf-8")
    report = sync_knowledge(knowledge_dir)
    assert report.deactivated == 2
    assert load_knowledge_context().founder_facts == ()


@pytest.mark.django_db
def test_removing_source_unverifies_a_fact(knowledge_dir):
    sync_knowledge(knowledge_dir)
    (knowledge_dir / "founder.md").write_text(
        "## Facts\n- Has led software teams since 2015.\n", encoding="utf-8"
    )
    sync_knowledge(knowledge_dir)
    assert load_knowledge_context().founder_facts == ()


@pytest.mark.django_db
def test_missing_or_empty_examples_are_handled(knowledge_dir):
    for path in (knowledge_dir / "writing_examples").iterdir():
        path.unlink()
    sync_knowledge(knowledge_dir)
    assert load_knowledge_context().writing_examples == ()

    (knowledge_dir / "writing_examples").rmdir()
    sync_knowledge(knowledge_dir)
    assert load_knowledge_context().writing_examples == ()


@pytest.mark.django_db
def test_missing_files_produce_warnings_not_errors(tmp_path):
    report = sync_knowledge(tmp_path)
    assert len(report.warnings) == 3
    context = load_knowledge_context()
    assert context.writing_rules == ""
    assert not context.has_verified_facts


@pytest.mark.django_db
def test_admin_unverified_knowledge_is_excluded(knowledge_dir):
    sync_knowledge(knowledge_dir)
    ContentKnowledge.objects.filter(content="Has led software teams since 2015.").update(
        is_active=False
    )
    assert load_knowledge_context().founder_facts == ()


@pytest.mark.django_db
def test_max_examples_limit(knowledge_dir):
    for i in range(4):
        WritingExample.objects.create(title=f"ex{i}", content="x", is_owner_approved=True)
    assert len(load_knowledge_context(max_examples=3).writing_examples) == 3


@pytest.mark.django_db
def test_repository_knowledge_files_load(capsys):
    call_command("sync_knowledge")
    context = load_knowledge_context()

    assert context.founder_facts == ()
    assert context.company_facts == ()
    assert len(context.subject_areas) == 13
    assert "MENA startup ecosystem" in context.subject_areas
    for rule in (
        "Short paragraphs",
        "buzzwords",
        "clickbait",
        "emojis",
        "fabricate",
        "Hook",
        "3,000 characters",
    ):
        assert rule in context.writing_rules, rule
    assert context.writing_examples == ()
    assert "subject areas: 13" in capsys.readouterr().out


def test_knowledge_dir_setting_points_at_repo_folder():
    assert Path(settings.KNOWLEDGE_DIR).name == "knowledge"
    assert (Path(settings.KNOWLEDGE_DIR) / "writing_style.md").exists()
