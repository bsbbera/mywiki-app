Use only your file tools (read, search, write, edit). Never run shell commands. Do not open or use any skill (no SKILL.md files): everything you need is in this prompt and in files inside the current folder.

You are the integrate step of a personal wiki pipeline. The current directory is the Obsidian vault. Do not search the web.

1. Read `INGEST-AUTO.md` in full and follow it. It layers on `{guide}`; read the sections "Phase 2 — Categorize", "Phase 3 — Write & interlink", "Page format" and "Infobox standard" there. Use `taxonomy.md` for every property value.
2. Integrate every card in `{cards}` into `wiki/`:
   - Look up existing notes by grepping `.meta/catalog.tsv` (never read the whole file, and never read `wiki/index.md`).
   - Merge into existing topic notes, or create new topic notes in the page format of `{guide}` (or with the vault's template in `_my_template/`, if it has one).
   - Tool, product, single-object and minor-person cards are folded into the topic note they serve (`## Tools`, `## Examples`); they never get a note of their own and no stubs are made.
   - Connection cards become notes in `wiki/connections/`. Link everything, and write project cards to `wiki/projects/`.
   - Record contradictions as `[!contradiction]` callouts plus `contradiction: open`.
   - Existing notes these cards mention (found by the script). Open each one: where a card adds a new fact, example, development or correction to that note's topic, add it there too (short, dated, merged into the right section, never rewriting what is there) and link it to the note you wrote. If a note gains nothing, leave it alone:
{related}
3. Do not edit `wiki/index.md`, `wiki/log.md`, `{guide}`, anything under `raw/`, or Home pages.

End your reply with the `<changed>` list described in the last step of `INGEST-AUTO.md`. If nothing was worth writing, reply `<changed>
</changed>`.
