---
title: "File extractor pipeline — XML `<file path=...>` contract"
aliases: [file-extractor, prompt-file-contract, xml-file-blocks]
tags: [backend, frontend, pipeline, prompt, contract]
sources:
  - "daily/2026-05-17.md"
created: 2026-05-17
updated: 2026-05-17
---

# File extractor pipeline — XML `<file path=...>` contract

Контракт между LLM и Omnia: модель ОБЯЗАНА возвращать файлы в формате `<file path="...">body</file>`. Регексп-парсер на бэкенде извлекает блоки, санитизирует пути, отдаёт `dict[path, body]` → commit_files → snapshot.

## Key Points

- **System prompt** ([`apps/api/src/omnia_api/services/prompt_builder.py:7`](../../apps/api/src/omnia_api/services/prompt_builder.py)) формулирует правило явно: «ВСЕГДА отдавай файлы целиком в формате `<file path="...">...</file>`. Если файл не нужно менять — не упоминай его. Если нужно удалить — отдай его с пустым содержимым.»
- **Backend parser** ([`apps/api/src/omnia_api/services/file_extractor.py:8`](../../apps/api/src/omnia_api/services/file_extractor.py)): `re.compile(r'<file\s+path="(?P<path>[^"]+)"\s*>(?P<body>.*?)</file>', re.DOTALL)`. Non-greedy `.*?` — корректно ловит цепочку блоков подряд.
- **Path sanitize:** запрещены префиксы `/`, `~`, `.git/`, `.git\`; запрещён `..` в любом месте; запрещён null byte. Раскладка через `PurePosixPath`, отбрасываем абсолютные. Лимиты: 100 файлов на ответ, 2 MiB на файл.
- **Frontend mirror** ([`apps/web/src/lib/parse-assistant.ts`](../../apps/web/src/lib/parse-assistant.ts)): такой же regex (`/<file\s+path="([^"]+)"\s*>/g`), но поддерживает **partial body** (`closed: false`) — пока `</file>` не пришло, отдаёт всё что есть до конца строки. Используется для streaming preview и file-чипов в чате.
- **Single source of truth — system prompt и regex.** Если меняем формат — обновляем оба файла + `apps/web/src/lib/parse-assistant.ts` + AGENTS.md заметку.

## Details

### Что происходит после extract_files

[`apps/api/src/omnia_api/routers/messages.py:234-307`](../../apps/api/src/omnia_api/routers/messages.py):

```python
files = extract_files(accumulated)
if files:
    new_sha = await asyncio.to_thread(repo_svc.commit_files, project_id, files, msg, current_sha)
    snapshot = Snapshot(project_id, commit_sha=new_sha, prompt_text, model_id, parent_id)
    session.add(snapshot)
    project.current_snapshot_id = snapshot.id
    msg.snapshot_id = snapshot.id
    await session.commit()
    await asyncio.to_thread(enqueue_preview, snapshot.id)
    await publish_event(project_id, "snapshot.created", {...})
else:
    # T1-fix 2026-05-17: ранее silent fail, теперь явный llm.error
    await publish_event(project_id, "llm.error", {message_id, error: "Модель не вернула ни одного файла..."})
```

### Какие модели лучше / хуже следуют формату

| Модель | Надёжность XML-формата | Заметки |
|---|---|---|
| Claude Sonnet 4.6 / Opus 4.7 / Haiku 4.5 | ★★★★★ | Anthropic-модели идеально держат структурированный вывод |
| GPT-4.1 / GPT-5 mini | ★★★★☆ | Иногда оборачивает в дополнительный markdown-блок |
| YandexGPT 5 | ★★★☆☆ | Случается прозаический ответ без файлов |
| Qwen 3 Coder | ★★★★☆ | Чаще соблюдает чем нет |
| GigaChat 2 / Pro / Max | ★★☆☆☆ | Регулярно возвращает markdown `\`\`\`html` вместо XML или вообще прозаическое описание. См. [[knowledge/concepts/zero-files-silent-failure]] |

Дефолтная модель на проде — `claude-haiku-4-5` именно из-за надёжности формата.

### Frontend parser — ключевые экспорты

```ts
export type AssistantPart =
  | { kind: "text"; text: string }
  | { kind: "file"; path: string; body: string; closed: boolean };

parseAssistantContent(content): AssistantPart[]
collectStreamingFiles(content): Record<string, string>          // только closed
collectStreamingFilesPartial(content): Record<string, string>   // включает open
extractStreamingBody(content): string | null                    // последний index.html
buildStreamingPreview(content): string | null                   // index.html с inlined CSS/JS
formatBytes(bytes): string                                       // "4.2 KB"
```

Используется в `ChatMessage.tsx`, `StreamingPreviewFrame.tsx`, `PreviewFrame.tsx`.

### Что если формат поменяется на tool-calling

Заметка для будущего: для Anthropic / OpenAI можно перейти на native tool-calling (`write_file`, `delete_file`, ...) вместо XML парсинга — намного надёжнее для совместимых моделей. XML оставить как fallback для не-tool-calling провайдеров (GigaChat, YandexGPT). Это «T3» из плана `2026-05-17`. Триггер для миграции — когда юзеры стабильно жалуются на «пустые ответы» от не-Anthropic моделей.

## Related Concepts

- [[knowledge/concepts/zero-files-silent-failure]] — silent fail когда parser вернул {}
- [[knowledge/concepts/proxyapi-anthropic-route]] — главный канал для надёжной генерации
- [[knowledge/concepts/realtime-streaming-preview]] — потребитель partial-body parsing

## Sources

- [[daily/2026-05-17.md]] — initial documentation
