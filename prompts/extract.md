Use only your file tools (read, search, write, edit). Never run shell commands. Do not open or use any skill (no SKILL.md files): everything you need is in this prompt and in files inside the current folder.

You are the extract step of a personal wiki pipeline. Do not edit or create any file. Do not search the web.

Read the chat transcript at `{staged}`. Then read the sections "What counts as knowledge" and "Card format" in `INGEST-AUTO.md`, and use `taxonomy.md` for property values.

Turn the chat into knowledge cards:
- Cards are **topics**: principles, mechanisms, theories, cultural and historical subjects, stories of cause and effect. Name each card after the broad topic, never after a tool, file, product, bug or single object.
- Put the why (maths, psychology, philosophy behind it) in `why:`, named objects in `examples:`, and tools and products only in `tools:` of the topic they serve.
- When the chat links ideas across fields, add a `kind: connection` card with the chain and the reason for each step.
- Drop debugging, logs, reports, the user's own code and setup, tutorials, tool trivia and small talk.
- If the chat moved one of the user's own projects forward (see `project:` in the header), add one `kind: project` card saying what changed.
- One card per topic; several facts about the same topic go in one card. Keep facts short and in plain words. Merge repeats.
- Chat answers can be wrong. Keep what the chat concluded, not the dead ends.

Reply with the cards wrapped exactly like this, and nothing else:
<cards>
### ...
</cards>
If there is nothing durable, reply `<cards>NONE</cards>`.
