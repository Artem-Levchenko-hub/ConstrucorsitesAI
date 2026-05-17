---
title: "Silent failure при 0 файлах в LLM-ответе (regression до 2026-05-17)"
aliases: [zero-files-silent-fail, llm-done-without-snapshot]
tags: [backend, regression, ux, post-mortem, llm]
sources:
  - "daily/2026-05-17.md"
created: 2026-05-17
updated: 2026-05-17
---

# Silent failure при 0 файлах в LLM-ответе

До 2026-05-17 backend молча финализировал ассистентское сообщение когда `extract_files()` возвращал пустой dict. UI видел только `llm.done` со счётчиками токенов — думал «всё хорошо», но snapshot не создавался, preview оставался на предыдущей версии. Юзер думал что система сломана. Особенно часто триггерилось при работе с GigaChat.

## Key Points

- **Симптом:** юзер шлёт промпт, видит как модель печатает в чат, ответ заканчивается, `↑ N · ↓ M tokens` появляется — но preview iframe не обновляется, в timeline нет нового snapshot.
- **Root cause:** [`apps/api/src/omnia_api/routers/messages.py:308-311`](../../apps/api/src/omnia_api/routers/messages.py) (до фикса) — ветка `else` после `if files:` просто финализировала message без snapshot_id и БЕЗ публикации `llm.error`. Только `llm.done` шёл через WS.
- **Почему модель не возвращала файлы:** обычно GigaChat выдавал ответ в формате markdown ```\`\`\`html ... \`\`\```` или прозу — regex `<file path="...">...</file>` не находил совпадений. См. [[knowledge/concepts/file-extractor-pipeline]] — таблица надёжности моделей.
- **Fix (commit `84a8986`):** в `else` теперь явный `publish_event(project_id, "llm.error", {message_id, error: hint})` с подсказкой про Haiku/Sonnet. UI разлочивается, юзер понимает что произошло.
- **Дополнительная защита:** `llm.error` handler в `usePromptStream.ts` теперь тоже ставит `tokens_out: 0` в кэше — иначе сообщение «висело» как стримящееся даже после ошибки.

## Details

### Reproducer (теперь воспроизводится корректно — с явной ошибкой)

1. Выбрать GigaChat 2 в Model Selector
2. Сабмитнуть «Создай лендинг кофейни в тёплых тонах»
3. Модель пишет в чат прозаический ответ (часто без `<file>` тегов)
4. **До фикса:** Чат показывает токены, preview не меняется → юзер недоумевает
5. **После фикса:** В чате появляется `[Ошибка: Модель не вернула ни одного файла в формате <file path="...">...</file>...]`, очевидный сигнал переключиться на Haiku/Sonnet

### Связанная проблема — 16-минутный курсор

Параллельно при GigaChat был случай когда курсор «крутился» 16+ минут. Причина — `sber.py:156` имеет `timeout=60.0`. Если GigaChat отвечает дольше или зависает (например, OAuth токен истёк mid-stream), httpx кидает `UpstreamProviderError`. Exception ловится в `messages.py:324`, fallback в `except` пытается отметить сообщение завершённым — но если сессия БД уже мертва, fallback падает. Сообщение остаётся с `tokens_out IS NULL`, фронт держит курсор бесконечно.

Это **отдельный класс багов** — частично mitigated в этой же сессии:
- `usePromptStream.cancel()` теперь принудительно ставит `tokens_out=0` в кэше → UI разлочивается даже если backend завис
- TODO: настоящий `/messages/:id/cancel` эндпоинт на бэке для прерывания стрим-лупа

### Урок

**Любой terminal state pipeline-а должен публиковать событие.** Тихие проходы через `else` без сигнала клиенту — гарантированный путь к «продукту, который не работает» в восприятии пользователя. Правило: если есть `if X: ... else: ...` где `X` влияет на UX, в `else` должен быть либо альтернативный success-сигнал, либо `error` с осмысленной подсказкой.

## Related Concepts

- [[knowledge/concepts/file-extractor-pipeline]] — где regex может не найти ничего
- [[knowledge/concepts/proxyapi-anthropic-route]] — Haiku как надёжная альтернатива GigaChat

## Sources

- [[daily/2026-05-17.md]] — discovery + fix, commit `84a8986`
