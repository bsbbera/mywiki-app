---
title: How this wiki is kept
publish: false
---
# How this wiki is kept

The house style for every note, whoever writes it: you, an AI agent you ask by hand, or MyWiki's automatic runs. Edit it to taste; MyWiki never overwrites it. Automatic runs also follow `INGEST-AUTO.md`, which wins where the two differ.

## Purpose

A life-long, interlinked knowledge base: one note per idea, linked to the ideas around it, so it reads like your own encyclopedia and grows with every chat.

## Folders

`wiki/` holds two kinds of top-level folder:

- **Fields of knowledge**, named in `taxonomy.md` (`technology/`, `science/`, `arts/`, `history/`, …), each split into subfields (`science/astronomy/`). Never deeper than `field/subfield/sub-subfield`.
- **Cross-cutting folders:** `people/`, `books/`, `projects/` (your own work) and `connections/` (chains of ideas across fields).

Operational files, never deleted or moved: `wiki/index.md` (catalog of every page) and `wiki/log.md` (record of manual ingests). MyWiki keeps its own monthly logs in `wiki/logs/` and generated dashboards in `wiki/_dashboards/`.

## Phase 1 — Understand

Read the whole source, then `wiki/index.md` (or grep `.meta/catalog.tsv`) to see what already exists.

## Phase 2 — Categorize

- A **person** goes to `wiki/people/<kebab-name>.md`, a **book** to `wiki/books/<kebab-title>.md`.
- Everything else goes to the field and subfield it belongs to, from `taxonomy.md`. Create a subfield folder when none fits; ask before creating a new top-level field.
- Prefer adding to an existing note over making a new one.

## Phase 3 — Write & interlink

1. One idea per note. File names are lowercase-with-hyphens (`survivorship-bias.md`).
2. Link related notes inline with `[[wikilinks]]`, using bare file names (`[[survivorship-bias]]`).
3. Set the frontmatter below; take every property value from `taxonomy.md`.
4. End every note with a `## Related pages` block, grouped by theme.

## Page format

```markdown
---
title: Survivorship bias
Created: 2026-01-31
date modified: 2026-01-31
aliases: []
category: Psychology
tags:
  - topic/decision-making
publish: true
type: concept
field: psychology
subfield: cognitive
---

Opening paragraph: what it is, in plain words.

## Why it works

## Examples

## Interesting facts

## Related pages

> [!note] Thinking errors
> - [[confirmation-bias]]
> - [[base-rate-fallacy]]
```

## Infobox standard

Only named entities (a person, book, work, place, event) get an infobox, right after the frontmatter. Concepts, stories and connections get none.

```markdown
> [!infobox]
> # Ada Lovelace
> ###### Person
> | | |
> | --- | --- |
> | **Born** | 1815 |
> | **Known for** | First published computer program |
```

Inside table cells, escape a link's pipe: `[[note\|Label]]`.

## Rules

- Never change anything under `raw/`: sources stay as they arrived.
- Write in plain, clear language, as if explaining to a smart friend.
- If two sources disagree, keep both claims and say so.
