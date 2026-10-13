You are the extract step of a personal wiki pipeline. Everything you need is in this message. Do not use any tools: don't read files, run commands, open skills, or search the web. Just reply.

Turn the chat transcript at the end into knowledge cards:
- Cards are **topics**: principles, mechanisms, theories, cultural and historical subjects, stories of cause and effect. Name each card after the broad topic, never after a tool, file, product, bug or single object.
- Put the why (maths, psychology, philosophy behind it) in `why:`, named objects in `examples:`, and tools and products only in `tools:` of the topic they serve.
- When the chat links ideas across fields, add a `kind: connection` card with the chain and the reason for each step.
- Drop debugging, logs, reports, the user's own code and setup, tutorials, tool trivia and small talk.
- If the chat moved one of the user's own projects forward (see `project:` in the header), add one `kind: project` card saying what changed. Use the name of an existing project if one fits: {projects}
- One card per topic; several facts about the same topic go in one card. Keep facts short and in plain words. Merge repeats.
- Chat answers can be wrong. Keep what the chat concluded, not the dead ends.

Reply with the cards wrapped exactly like this, and nothing else:
<cards>
### ...
</cards>
If there is nothing durable, reply `<cards>NONE</cards>`.

=== Rules (from INGEST-AUTO.md) ===
{rules}

=== Property vocabulary (taxonomy.md) ===
{taxonomy}

=== Chat transcript ===
{chat}
