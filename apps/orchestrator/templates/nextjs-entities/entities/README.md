# entities/ — the data model

Every business object is one JSON file here: `entities/<Name>.json`. The engine
(`src/app/api/entities/**`) reads these at request time, so adding or editing an
entity takes effect immediately — **no migration, no DB code, no restart**. All
rows live in the single `records` table (`data` JSONB), discriminated by entity.

## Schema format

```json
{
  "name": "Task",
  "access": "owner",
  "fields": {
    "title":    { "type": "string",  "required": true },
    "done":     { "type": "boolean", "default": false },
    "priority": { "type": "enum", "options": ["low", "medium", "high"], "default": "medium" },
    "due":      { "type": "date" },
    "notes":    { "type": "text" }
  }
}
```

- `name` — must match the filename (`Task.json` → `"Task"`). Letters/digits/underscore, starts with a letter.
- `access` — who may read/write:
  - `owner` (default) — auth required; each user only ever sees/edits their own rows.
  - `public` — anyone (even signed-out) can read; writing needs auth and you can only edit rows you created.
  - `admin` — only users with role `admin`.
- `fields` — a map of field → `{ type, required?, default?, options? }`.
  - `type`: `string` | `text` | `number` | `boolean` | `date` (ISO string) | `enum` (needs `options`) | `reference` (a relation — needs `entity`, the target name; stores that row's id, filterable, and `?expand=<field>` embeds the related row).
  - `required: true` — must be present on create.
  - `default` — applied on create when omitted.

## Reserved / automatic fields

Every row automatically gets `id`, `created_by`, `created_at`, `updated_at` —
do **not** declare them as fields. The engine sets and returns them.

## Using entities from the frontend

```ts
import { entities } from "@/lib/sdk";
const tasks = await entities.Task.list({ sort: "due", order: "asc" });
await entities.Task.create({ title: "Купить молоко" });
```

See `src/lib/sdk/index.ts` for the full client API.
