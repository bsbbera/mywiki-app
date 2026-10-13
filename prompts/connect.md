Use only your file tools (read, search, write, edit). Never run shell commands. Do not open or use any skill (no SKILL.md files): everything you need is in this prompt and in files inside the current folder.

You are the connect step of a personal wiki pipeline. The current directory is the Obsidian vault. Do not search the web.

Your job: find **{n}** meaningful long connections between ideas in different fields, and write each as a connection note. Example of the kind wanted: luck → success → power laws (mathematics) → survivorship bias (psychology) → Stoic acceptance (philosophy).

1. Read `INGEST-AUTO.md`, section "What counts as knowledge" and step 6 of "Integrate steps", and the `connection` type in `taxonomy.md`.
2. Read `.meta/topics.tsv`: one line per note with path, title, field, tags and a one-line summary. Lines starting with `*` were written or changed recently: start from those.
3. Existing connection notes (do not repeat these chains): {existing}
4. For each connection:
   - The chain has 3 to 6 steps and spans at least 3 different fields. At least 3 steps must be existing notes (link them with `[[bare-filename]]`).
   - Every step needs a real reason it leads to the next: a mechanism, a piece of maths, a historical cause, a psychological effect. No vague "both are about creativity". If you cannot say why, pick another chain.
   - Read the notes you link before writing, so the reasons match what they say.
   - Write `wiki/connections/<kebab-title>.md` with frontmatter `title`, `Created`, `date modified`, `type: connection`, `fields` (list), `chain` (the arrow line), `tags` (1–3 `topic/…`), `source: connect`, `confidence: medium`, `schema: 3`, `publish: true`. Body: an opening paragraph on why the chain matters, `## The chain` with one short paragraph per step, then `## Related pages`.
   - In each linked topic note, add one line under `## Related pages` (or a `## Connections` section if there is none) pointing back to the connection note. Change nothing else in those notes.
5. Do not edit `wiki/index.md`, `wiki/log.md`, `{guide}`, anything under `raw/`, or dashboards.

End your reply with:
<changed>
created: wiki/connections/<file>.md
modified: <path>
</changed>
