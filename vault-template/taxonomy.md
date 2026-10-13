---
title: Taxonomy
publish: false
---
# Taxonomy

The fixed vocabulary for every note's properties. Ingest (manual or automatic) uses only these values. A new value is allowed only when nothing here fits; add it to this file in the same run so the list stays the single source of truth.

## Two axes: where it lives, what it is

A note has one **home** (folder = `field` + `subfield`) and one **type**. Everything else is a property you can filter on. Never create a folder deeper than `field/subfield/sub-subfield`.

## `field` (top folder under `wiki/`) and common `subfield`s

| field | subfields (examples, extend as needed) |
| --- | --- |
| technology | computer-science, software-engineering, data-engineering, databases, cloud/gcp, cloud/aws, cloud/azure, cloud/databricks, machine-learning, ai, tools, guides |
| science | physics, chemistry, biology, mathematics, astronomy, medicine, earth-science |
| philosophy | ethics, metaphysics, epistemology, logic, paradoxes, eastern-philosophy |
| psychology | cognitive, behavioural, social, clinical, case-studies |
| history | ancient, medieval, modern, independence-movements, science-history, tech-history |
| finance | investing, financial-statements, financial-ratios, economics |
| arts | painting, photography, design, design-systems, music, cinema, literature, architecture, typography |
| culture | food, names, festivals, mythology, languages, traditions, games |
| places | countries, cities, landmarks |
| career | interviews, skills |

`people/`, `books/`, `projects/` and `connections/` are cross-cutting folders: the note lives there, and its `fields` property says which fields it belongs to. Automatic ingest never writes to `technology/tools/`: tools are bullets inside topic notes.

## `type` (what the note is)

- `concept`: an idea, theory, pattern or principle (CAP theorem, cognitive dissonance)
- `person`: a human being (lives in `people/`)
- `work`: a made thing with an author, like a book, paper, painting, film or song. Set `work_kind`: book | paper | painting | photograph | film | music | building | other
- `product`: software, a cloud service, a tool or a device (BigQuery, Obsidian). **Manual ingest only**; automatic ingest lists tools under `## Tools` in a topic note instead
- `org`: a company, institution or movement
- `place`: a location
- `event`: something that happened at a time (Partition of India, Dartmouth workshop)
- `story`: a narrative about impact or causation ("How the Turing machine changed the world"). It links to the concept, person and work it is about.
- `project`: your own project (lives in `projects/`)
- `connection`: a chain of ideas across three or more fields and why each step leads to the next ("How luck shapes success"). Lives in `connections/`, with `fields` (list) and `chain` (the arrow line)
- `hub`: a navigation or overview page

## `angle` (what kind of knowledge the note holds)

`fundamentals` · `interview` · `history` · `news` · `trivia`

## Entity properties (filterable)

- **person**: `roles` (scientist, mathematician, philosopher, polymath, artist, painter, photographer, designer, writer, poet, musician, film-maker, freedom-fighter, leader, entrepreneur, engineer, psychologist, economist, investor), `gender` (male | female | non-binary | unknown), `nationality`, `era` (ancient | medieval | early-modern | 19th-c | 20th-c | 21st-c), `born`, `died`, `fields`, `movement`
- **work**: `work_kind`, `creator`, `year`, `genre`, `medium`, `movement`, `fields`
- **product**: `vendor`, `category`, `fields`
- **place / event / culture notes**: `region`, `era`

## Pipeline properties

- `source`: manual | claude-code | claude-ai | devin | antigravity | quire | news | social (where the knowledge came from; `news` = scheduled last-30-days research runs)
- `confidence`: high | medium | low. Chat-derived facts that were not checked against a source are `medium`.
- `contradiction`: open | resolved. `open` means the note has a `[!contradiction]` callout waiting for you.
- `schema: 2`: the note follows this taxonomy. `schema: 3`: it also follows the topic-note rules in `INGEST-AUTO.md` (why it works, examples, tools as bullets).

## Topic tags

Every automatic note carries 1–3 `topic/…` tags. They group notes across folders, so a design note and a psychology note can share `topic/perception`. Current topics:

`topic/design-principles` · `topic/colour` · `topic/typography` · `topic/perception` · `topic/creativity` · `topic/decision-making` · `topic/probability` · `topic/statistics` · `topic/logic` · `topic/physics` · `topic/games-and-play` · `topic/history-of-science` · `topic/astronomy` · `topic/photography` · `topic/ai-agents` · `topic/machine-learning` · `topic/software-craft` · `topic/knowledge-and-learning` · `topic/neuroscience` · `topic/mental-health` · `topic/evolution` · `topic/economics` · `topic/cinema` · `topic/music`

## Tags

Tags are for cross-cutting themes only, never a copy of `field` or `type`. Use lowercase kebab-case. Current themes: `ai-history`, `turning-point`, `women-in-science`, `open-source`, `interview-trap`, `paradox`, `mental-model`, `certification`, `design-systems`, `accessibility`.
