"""Class/Subject/Chapter title encoding for SuperDocs documents.

Every document this app uploads to SuperDocs gets a title of the fixed
form::

    Class :: Subject :: Chapter

for example ``Class 8 :: Science :: Ch. 7: Photosynthesis``. The Library
page later lists *all* documents in the SuperDocs Files list — including
ones uploaded outside this app — and groups them by Class › Subject ›
Chapter purely by parsing these titles, with no sidecar database.

Encoding rules
--------------
* The delimiter between the three fields is exactly ``" :: "`` (space,
  two colons, space).
* Fields are trimmed on both encode and decode, so stray surrounding
  whitespace cannot split one class into two grouping keys.
* :func:`parse_title` splits on the first two delimiters only, so a
  *chapter* title that itself contains ``" :: "`` still round-trips.
* Class and Subject must not contain ``" :: "`` (it would be indistinguishable
  from a field separator when parsing); :func:`assemble_title` rejects such
  values rather than producing an unparseable title.
* Empty or whitespace-only fields are rejected by :func:`assemble_title`.
* :func:`parse_title` never raises on unexpected *string* input: any title
  that does not match the format (wrong number of fields, empty field)
  yields ``None`` so foreign documents simply don't appear in the grouping.

Validation failures here raise plain :exc:`ValueError` rather than
:class:`~superdocs_orchestrator.exceptions.SuperDocsError`: they signal
malformed caller input, not a SuperDocs API problem.
"""

from __future__ import annotations

from dataclasses import dataclass

DELIMITER = " :: "


@dataclass(frozen=True)
class DocumentTitle:
    """The three decoded fields of an encoded document title."""

    class_name: str
    subject: str
    chapter: str


def _require_nonempty(name: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def assemble_title(class_name: str, subject: str, chapter: str) -> str:
    """Assemble ``Class :: Subject :: Chapter`` from its three parts.

    Fields are trimmed before assembly, so ``assemble_title("Class 8 ",
    ...)`` produces the same title as ``assemble_title("Class 8", ...)``.

    Raises:
        ValueError: if any part is empty/whitespace-only, or if
            ``class_name`` / ``subject`` contain the ``" :: "`` delimiter
            (which would make the title unparseable). The chapter may
            contain the delimiter, since parsing splits on the first two
            delimiters only.
    """
    fields = (
        ("class_name", class_name.strip()),
        ("subject", subject.strip()),
        ("chapter", chapter.strip()),
    )
    for name, value in fields:
        _require_nonempty(name, value)
    for name, value in fields[:2]:
        if DELIMITER in value:
            raise ValueError(f'{name} must not contain {DELIMITER!r}: {value!r}')
    return DELIMITER.join(value for _, value in fields)


def parse_title(title: str) -> DocumentTitle | None:
    """Parse an encoded title back into its three parts.

    Returns ``None`` when *title* does not match the expected format, so
    documents uploaded outside this app's flow degrade gracefully instead
    of crashing callers.
    """
    parts = title.split(DELIMITER, 2)
    if len(parts) != 3:
        return None
    class_name, subject, chapter = (part.strip() for part in parts)
    if not class_name or not subject or not chapter:
        return None
    return DocumentTitle(class_name, subject, chapter)
