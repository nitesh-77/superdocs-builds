"""Tests for the Class :: Subject :: Chapter title-encoding scheme."""

from __future__ import annotations

import pytest

from superdocs_orchestrator.titles import (
    DocumentTitle,
    assemble_title,
    parse_title,
)


def test_round_trip_basic() -> None:
    title = assemble_title("Class 8", "Science", "Photosynthesis")
    assert title == "Class 8 :: Science :: Photosynthesis"
    assert parse_title(title) == DocumentTitle(
        class_name="Class 8",
        subject="Science",
        chapter="Photosynthesis",
    )


@pytest.mark.parametrize(
    ("class_name", "subject", "chapter"),
    [
        ("Class 8", "Science", "Ch. 7: Photosynthesis"),
        ("Class 10", "History", "World War II, 1939-1945"),
        ("Class 6", "Mathematics", "Fractions & Decimals (Part 2)"),
        ("Class 12", "English", "Ode on a Grecian Urn"),
        ("Nursery", "Rhymes", "Twinkle, Twinkle!"),
    ],
)
def test_round_trip_realistic_titles(
    class_name: str,
    subject: str,
    chapter: str,
) -> None:
    parsed = parse_title(assemble_title(class_name, subject, chapter))
    assert parsed == DocumentTitle(
        class_name=class_name,
        subject=subject,
        chapter=chapter,
    )


@pytest.mark.parametrize(
    "title",
    [
        "Random upload with no separators",
        "Class 8 - Science - Photosynthesis",
        "Class 8 :: Science",
        "",
        " :: Science :: Photosynthesis",
        "Class 8 ::  :: Photosynthesis",
        "Class 8 :: Science :: ",
    ],
)
def test_unparseable_titles_return_none(title: str) -> None:
    assert parse_title(title) is None


@pytest.mark.parametrize(
    ("class_name", "subject", "chapter"),
    [
        ("", "Science", "Photosynthesis"),
        ("Class 8", "   ", "Photosynthesis"),
        ("Class 8", "Science", ""),
    ],
)
def test_assemble_rejects_empty_fields(
    class_name: str,
    subject: str,
    chapter: str,
) -> None:
    with pytest.raises(ValueError):
        assemble_title(class_name, subject, chapter)


@pytest.mark.parametrize(
    ("class_name", "subject", "chapter"),
    [
        ("Class :: 8", "Science", "Photosynthesis"),
        ("Class 8", "Science :: Fiction", "Photosynthesis"),
    ],
)
def test_assemble_rejects_delimiter_in_class_or_subject(
    class_name: str,
    subject: str,
    chapter: str,
) -> None:
    with pytest.raises(ValueError):
        assemble_title(class_name, subject, chapter)


def test_chapter_may_contain_delimiter() -> None:
    title = assemble_title("Class 8", "Science", "Rocks :: Igneous")
    assert parse_title(title) == DocumentTitle(
        class_name="Class 8",
        subject="Science",
        chapter="Rocks :: Igneous",
    )


def test_fields_are_trimmed_for_stable_grouping_keys() -> None:
    trimmed = assemble_title("Class 8 ", " Science", "Photosynthesis")
    untrimmed = assemble_title("Class 8", "Science", "Photosynthesis")
    assert trimmed == untrimmed
    assert parse_title(" Class 8 :: Science :: Photosynthesis ") == parse_title(
        untrimmed
    )
