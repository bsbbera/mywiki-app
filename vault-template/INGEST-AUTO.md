---
title: Automatic ingest rules
publish: false
---
# Automatic ingest (chat to wiki)

These rules are for the **automatic** pipeline, which turns saved AI chats into notes. They sit on top of `WIKI.md` and use the same folders, page format, infobox standard and `Related pages` block. Where this file and `WIKI.md` differ, this file wins for automatic runs only.

Vocabulary for every property: `taxonomy.md`.

## What differs from WIKI.md

1. **No web search, no browsing.** The research already happened in the chat. Facts come from the chat, plus your own knowledge only for standard facts. If you are unsure, leave it out.
2. **No discussion or approval step.** Write the notes directly.
3. **No `plan.md`.** Do not edit `wiki/index.md` or `wiki/log.md` either: the script records runs and keeps the index section up to date.
4. **Topic notes, not item notes.** A note is about an idea worth knowing in a year, never about a single tool, file, product, bug or object. 250–450 words of body text, not counting the infobox and `Related pages`.
5. **No stubs.** Automatic runs never make a placeholder note for a person or book. A person gets a note only under the person rule below.
6. **Language:** plain, human, to the point. Write like explaining to a smart friend. Avoid filler, hype, "delve", "crucial", "in today's world", and stacked adjectives.

## What counts as knowledge

The wiki is a map of **ideas and how they connect**, not an inventory of things the user touched. Before writing anything, ask: *would a curious person look this topic up a year from now, and does it explain why something works?* If not, it is not a note.

**A note is a topic.** Good topics are principles, rules, mechanisms, theories, patterns, cultural or historical subjects, and stories of cause and effect:
- Design: "Colour proportion in design", "Grid systems and page canons", "Visual rhythm in editorial layout", "Contrast and accessibility".
- Games and play: "Rating systems and the maths of skill", "How chess engines evaluate a position".
- Culture: "Floor art traditions of India" (kolam, alpona and rangoli are **examples inside it**), "Folk music traditions", "Mathematics in folk art".
- Mind and decisions: "Luck and success", "Survivorship bias", "Knowledge hierarchy (data to wisdom)".

**Every topic note explains, in this order:** what it is → **why it works** (the maths, psychology, physics or philosophy behind it, when the chat gives any) → **examples** (named objects, artworks, places, games live here, one or two lines each) → **tools** (only as a bullet list, see below) → connections to other fields.

**Never a note of its own (fold it into the topic it serves):**
- **Tools, apps, products, file formats, CLIs, plugins, services, licences, settings and bugs** (Affinity, Canva, Procreate, Lightroom, ComfyUI, Claude Code, a CLI, an `.ai` file). Mention them under `## Tools` in the topic note they help with: one line each, what it is good for in that topic.
- **Single objects and instances** (one artwork, one game variant, one dataset, one symbol set, one craft). They become examples inside the broader topic: kolam → "Floor art traditions of India"; chess annotation symbols → "Chess notation"; a painting → the technique or movement it illustrates.
- **People who are not central to an idea.** Name them inline in the topic note. A person gets a note only when they are a major figure the user is likely to explore on their own (Leonardo da Vinci yes; the chemist who found one impurity, no).
- **How-to steps, config, and anything about the user's own code or setup.**

**Drop entirely:** bug hunts, stack traces, build logs, status reports, tutorials and click paths, small talk, chat about the chat.

**Projects are the one exception:** progress on the user's own projects is kept, but only as `project` cards (see below), never as knowledge notes.

**Prefer merging.** If an existing note covers the topic or a broader one, add to it. Create a new note only when the topic is broad enough to keep collecting facts from future chats. Too narrow? Go one level up.

**Connections are the point.** When a chat links ideas across fields (for example luck → success → probability → psychology → philosophy), keep that chain as a `connection` card. The reasoning between each step is the knowledge.

## Card format (the extract step writes these)

```
### <Topic name: broad enough to grow, e.g. "Colour proportion in design">
- kind: topic                # topic | connection | project
- type: concept              # concept | story | place | event | work | person (taxonomy)
- field: arts/design
- angle: fundamentals
- tags: topic/design-principles, topic/colour
- why: One or two lines on why it works: the maths, psychology or philosophy behind it.
- examples: kolam (dot-grid loops drawn at dawn), alpona (wet rice-paste art of Bengal)
- tools: Affinity (vector layout), Procreate (raster painting)
- links: Golden ratio, Gestalt principles
- props: gender=female; nationality=Indian; roles=scientist    # person only
- facts:
  - One short fact per line, in plain words.
- interview:
  - Q: ... | A: ... | Trap: ...
- contradiction: <only if the chat itself disagreed with an earlier claim>
```

A **connection** card names the chain and says why each step leads to the next:

```
### How luck shapes success
- kind: connection
- chain: Luck → Success → Power laws (mathematics) → Survivorship bias (psychology) → Stoic acceptance (philosophy)
- fields: psychology, science/mathematics, philosophy
- tags: topic/decision-making, topic/probability
- steps:
  - Luck → Success: outcomes in winner-take-all fields depend on small random advantages early on.
  - Success → Power laws: compounding turns small early edges into very large late gaps.
```

A **project** card uses `kind: project`, `field: projects`, and has `- status: active | paused | done` plus `- progress:` lines saying what moved forward. Say what changed, not the debugging.

## Integrate steps (per cards file)

1. **Find existing notes**: grep `.meta/catalog.tsv` for the topic, its likely aliases, and the broader topic it may belong to. Never read all of `catalog.tsv` or `wiki/index.md` into context. Grep is enough.
2. **Fold, don't spawn.** A card about a tool, product, file format, single object or minor person goes into the topic note it serves (under `## Tools` or `## Examples`), creating that topic note if needed. Never create a note in `wiki/technology/tools/`.
3. **Existing note**: merge only the new facts, keep it within length, and don't duplicate what's there. Bump `date modified`.
4. **New note**: follow the page format in `WIKI.md` (or the vault's own template in `_my_template/`, if it has one). Body sections, in order: opening paragraph (what it is), `## Why it works`, `## Examples`, `## Tools` (only if any), `## Interesting facts`, `## Interview questions` (only for study subjects), `## Related pages`. Frontmatter: `title`, `Created`, `date modified`, `aliases`, `category`, `tags`, `banner`, `publish: true`, plus `type`, `field`, `subfield`, `angle`, `source`, `confidence: medium`, `schema: 3`.
   `category` keeps the vault's human-readable style (e.g. `Design`, `Psychology`, `Culture`); look at a sibling note in the same folder.
5. **Tags**: 1–3 `topic/…` tags from `taxonomy.md`, plus a theme tag only if one fits. Add a new `topic/…` value to `taxonomy.md` only when nothing fits.
6. **Connection cards** become notes in `wiki/connections/<kebab-title>.md` with `type: connection`, `fields` (list), `chain` (the arrow line), and tags. Body: one opening paragraph, then `## The chain`, one short paragraph per step that says *why* it leads to the next, each step linking the topic note it rests on. Merge into an existing connection note if one covers the same chain.
7. **Infobox**: only person, work, place and event notes get one. Concepts, stories and connections get none.
8. **Links**: add `[[wikilinks]]` inline, using bare filenames, to topic notes that exist (grep the catalog). End with the grouped `## Related pages` block from `WIKI.md`.
9. **Contradictions**: if a card disagrees with an existing note, keep both claims and resolve nothing:
   ```
   > [!contradiction] Open
   > Existing: <claim> · New (<source>, <date>): <claim>
   ```
   Set `contradiction: open` in that note's frontmatter.
10. **Projects**: first list `wiki/projects/`. If the work belongs to an existing project, add to that note. Otherwise create one named after the product or goal (e.g. `garden-planner`, `thesis`), never after a single chat. Path: `wiki/projects/<kebab-name>.md`, with frontmatter `type: project`, `status`, `last_active: YYYY-MM-DD`, `summary`. Under `## Progress`, add dated one-line entries, newest first.
11. **No Home or hub pages**: MyWiki writes the dashboards itself.
12. **Finish** your reply with the list of files you touched:
   ```
   <changed>
   created: wiki/arts/design/colour-proportion.md
   modified: wiki/culture/traditions/floor-art-traditions-of-india.md
   </changed>
   ```
