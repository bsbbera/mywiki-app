Use only your file tools (read, search, write, edit). Never run shell commands. Do not open or use any skill (no SKILL.md files): everything you need is in this prompt and in files inside the current folder.

You are tidying a personal wiki (the current directory is the Obsidian vault) so it holds topic notes instead of many small item notes. Do not search the web.

1. Read `INGEST-AUTO.md` sections "What counts as knowledge" and "Integrate steps", and `taxonomy.md` for property values.
2. Topic note: `{target}` titled "{title}". {exists}
3. Read each of these notes and fold their durable knowledge into the topic note:
{sources}
   - Keep what explains the idea, why it works (maths, psychology, physics, history), good examples and surprising facts. Named objects, works and people become lines under `## Examples` or inline mentions. Tools and products become one-line bullets under `## Tools`.
   - Drop tool trivia, version numbers, install steps, settings, bugs and anything about the user's own setup.
   - The result reads as one coherent note, 300–600 words of body text, following the section order in step 4 of "Integrate steps". Merge repeats. Keep useful `[[wikilinks]]` to notes that are not in the list above.
   - Frontmatter as in step 4, with `schema: 3`, 1–3 `topic/…` tags, and `aliases` that include the titles of the folded notes so old links and searches still find it.
4. Do not delete, rename or edit the listed source notes; the script removes them after you finish. Do not edit any other note, `wiki/index.md`, dashboards, `{guide}`, `raw/`.

End your reply with:
<changed>
created: {target_full}
</changed>
(or `modified:` if it already existed)
