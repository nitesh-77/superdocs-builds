# Fixtures

Source documents every later ticket uploads, edits against, and exports from
via the SuperDocs API. All content here is **original and synthetic** — written
for this build; no real school's material is used anywhere.

## Files

| File | Kind | What makes it useful |
|---|---|---|
| [`standard-template.md`](standard-template.md) | Clean template | Exactly the four canonical sections — **Objectives, Materials, Procedure, Assessment** — with placeholder content. Uploaded once via `/v1/templates/upload`; reformat instructions reference it by name in prose (SuperDocs surfaces templates to the AI via semantic search, not an explicit parameter). |
| [`drafts/grade8-science-photosynthesis-draft.md`](drafts/grade8-science-photosynthesis-draft.md) | Messy draft | Wall-of-text notes with the **Materials** section missing entirely and a clearly trimmable aside. Exercises: ghost-template decoration + full reformat-to-template. |
| [`drafts/grade7-math-fractions-draft.md`](drafts/grade7-math-fractions-draft.md) | Messy draft | All four canonical sections present, but the **Procedure** section is one bloated run-on paragraph with near-duplicated steps, plus a trimmable closing note. Exercises: trim-redundancy instruction without structural change. |
| [`drafts/grade9-history-mughal-empire-draft.md`](drafts/grade9-history-mughal-empire-draft.md) | Messy draft | Shorthand lecture notes missing both **Objectives** and **Assessment**, containing TODO items. Exercises: multi-section ghost decoration + partial reformat. |

All drafts are Markdown (`.md`) — a supported SuperDocs upload format.

## Canonical section taxonomy

Fixed across the app and fixtures, matching the assigned card's wording verbatim:

```
Objectives · Materials · Procedure · Assessment
```

Optional sections (Standards Alignment, Differentiation) appear only when
already present in a source draft — never invented, never required.
