Use only your file tools (read, search, write, edit). Never run shell commands. Do not open or use any skill (no SKILL.md files): everything you need is in this prompt and in files inside the current folder.

You are migrating existing notes in this Obsidian vault (current directory) to the property schema in `taxonomy.md`. Read `taxonomy.md` first. Do not search the web.

For each note below:
1. Add the missing properties to its frontmatter: `type`, `field`, `subfield`, `angle`, and the entity properties for its type (people: `roles`, `gender`, `nationality`, `era`, `born`, `died`, `fields`; works: `work_kind`, `creator`, `year`, `genre`, `medium`, `fields`; products: `vendor`, `category` stays as-is, `fields`). Use only taxonomy values, and leave out what you don't know.
2. Keep every existing property and its value. `category`, `banner`, `title` and `aliases` stay untouched. In `tags`, keep every tag and add 1–3 `topic/…` tags from the "Topic tags" section of `taxonomy.md` that fit the note (skip if it already has them). If `field` or `subfield` is already set but clearly wrong for the content, correct it.
3. If the note is a named entity (person, work, product, org, place, event) and has no `[!infobox|right]` block, add one after the frontmatter using the matching template in `_my_template/`. Fill only fields you are sure of.
4. Do not rewrite, shorten or move the body text. Do not rename or move files.
5. As the last property, set `schema: 3` (replace an older `schema` value).

Notes:
{files}

End your reply with:
<changed>
modified: <path>
</changed>
