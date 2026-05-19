# Knowledge Base Index

> Auto-maintained by `scripts/compile.py`. Sorted by recency of last update.
> Stable anchor pages first, session-specific concepts below.

| Article | Summary | Compiled From | Updated |
| --- | --- | --- | --- |
| [[knowledge/project-context]] | Persistent project map: стек, агенты A/B/C, pipeline промпт→сайт, модели, workspace UI, доменный словарь. Инжектится в каждую сессию. | CLAUDE.md + docs/* | 2026-05-17 |
| [[knowledge/concepts/proxyapi-anthropic-route]] | claude-haiku-4-5 routes via proxyapi.ru native-Anthropic endpoint (`anthropic/claude-haiku-4-5` slug + `api_base=https://api.proxyapi.ru/anthropic` — без `/v1`, LiteLLM сам добавит `/v1/messages`). Override-механизм `_PROXY_ROUTES` в `services/litellm_router.py`. | daily/2026-05-17.md | 2026-05-17 |
| [[knowledge/concepts/realtime-streaming-preview]] | Долгоживущий iframe + morphdom postMessage DOM-diff: новые top-level элементы получают `data-omnia-new` и fade+slide-up через CSS keyframe. Bootstrap-HTML с Tailwind CDN + morphdom CDN. Дебаунс 150ms. | daily/2026-05-17.md | 2026-05-17 |
| [[knowledge/concepts/file-extractor-pipeline]] | `apps/api/.../file_extractor.py` regex `<file path="X">...</file>` + path sanitize. `prompt_builder.SYSTEM_PROMPT` мандатирует этот формат. Frontend mirror в `apps/web/.../parse-assistant.ts`. GigaChat почти всегда нарушает → 0 файлов → silent fail (см. zero-files-silent-failure). | daily/2026-05-17.md | 2026-05-17 |
| [[knowledge/concepts/zero-files-silent-failure]] | До 2026-05-17 backend молча финализировал ответ когда `extract_files` возвращал {} — UI получал только `llm.done` и думал «всё ок», но preview не обновлялся. Fix: явный `llm.error` с подсказкой про Haiku/Sonnet. | daily/2026-05-17.md | 2026-05-17 |
| [[knowledge/concepts/secondbrain-runtime]] | Core runtime conventions for session capture, docs ingest, compile and query cycles. | daily/2026-05-19.md | 2026-05-19 |
| [[knowledge/concepts/auto-memory-bridge]] | Bridge между Claude Code auto-memory (`~/.claude/projects/<hash>/memory/`) и SecondBrain: SHA-256 dedup, once-per-UTC-day trigger из session-start, append в daily log с категорией (Feedback/User/Project). | AGENTS.md | 2026-05-17 |
| [[knowledge/concepts/wiki-taxonomy-and-link-conventions]] | Stable taxonomy: concepts/connections/qa, wikilinks с полным relative path, YAML frontmatter обязателен (title/sources/created/updated), kebab-case имена. | AGENTS.md | 2026-05-17 |

| [[knowledge/concepts/daily-2026-05-17-summary]] | Compiled fallback summary for 2026-05-17.md | daily/2026-05-17.md | 2026-05-18 |

| [[knowledge/concepts/daily-ingestion-process]] | How session and docs events are appended into daily logs before compilation. | daily/2026-05-19.md | 2026-05-19 |

| [[knowledge/concepts/daily-2026-05-18-summary]] | Compiled fallback summary for 2026-05-18.md | daily/2026-05-18.md | 2026-05-18 |

| [[knowledge/concepts/gemini-integration-with-llm-gateway]] | Integration of Google Gemini 2.5 (Pro and Flash) into the LLM Gateway, including pricing, model routing, API key handling, and fallback mechanisms. This involved modifying several core files to support Gemini models a... | daily/2026-05-19.md | 2026-05-19 |

| [[knowledge/concepts/geo-block-circumvention-via-uk-proxy]] | Addressed Google's geo-blocking of Generative Language API for Russian IPs by routing Gemini requests through a UK-based HTTPS/SOCKS5 proxy. This was achieved by configuring container-level environment variables for p... | daily/2026-05-19.md | 2026-05-19 |

| [[knowledge/concepts/gemini-thinking-mode-and-token-budget-fix]] | Resolved an issue where Gemini 2.5 models would return minimal output ('От') due to an implicit 'thinking mode' consuming the token budget. The fix involved explicitly setting `max_tokens` and disabling `reasoning_eff... | daily/2026-05-19.md | 2026-05-19 |

| [[knowledge/concepts/api-crash-loop-fix-uv-run-pypi-timeout]] | Resolved a production API crash-loop caused by `uv run` attempting to resolve packages from pypi.org on every startup, leading to timeouts and container failures. | daily/2026-05-19.md | 2026-05-19 |

| [[knowledge/concepts/workspace-ui-polish-and-optimistic-insert]] | Implemented several UI/UX improvements in the workspace, including better model selector dropdowns, dynamic snapshot cards, adjusted column layouts, and an 'optimistic insert' for chat messages to improve perceived re... | daily/2026-05-19.md | 2026-05-19 |

| [[knowledge/connections/gemini-token-budget-and-output-quality]] | The 'thinking mode' quirk of Gemini 2.5 directly impacted the quality and completeness of its output, necessitating a specific fix in the LLM Gateway to ensure full responses were generated after the initial integration. | daily/2026-05-19.md | 2026-05-19 |

| [[knowledge/connections/deployment-stability-and-startup-performance]] | The API crash-loop caused by `uv run`'s network dependency during startup directly impacted the stability and deployment speed of the application, leading to a critical fix that streamlined container initialization. | daily/2026-05-19.md | 2026-05-19 |

| [[knowledge/connections/end-to-end-validation-of-new-features]] | The successful end-to-end test on a live project confirmed the correct functioning of Gemini integration, geo-block circumvention, and the improved UI/UX, validating the entire development effort. | daily/2026-05-19.md | 2026-05-19 |

| [[knowledge/concepts/claude-haiku-45-integration-via-proxyapiru]] | Integration of Claude Haiku 4.5 model into the LLM gateway using proxyapi.ru as an intermediary, including routing, API key management, and configuration updates. | daily/2026-05-17.md | 2026-05-19 |

| [[knowledge/concepts/real-time-streaming-preview-with-morphdom]] | Implementation of a live, per-element streaming preview for generated HTML content using a long-lived iframe and morphdom for efficient DOM patching. | daily/2026-05-17.md | 2026-05-19 |

| [[knowledge/concepts/enhanced-chat-ui-and-prompt-queueing]] | Improvements to the chat user interface including compact file chips, non-blocking input during streaming, and a robust prompt queuing mechanism. | daily/2026-05-17.md | 2026-05-19 |

| [[knowledge/concepts/code-view-and-snapshot-management]] | Introduction of a dedicated Code View for snapshots, a 'viewing old version' banner, and compact snapshot cards for improved navigation and context. | daily/2026-05-17.md | 2026-05-19 |

| [[knowledge/concepts/secondbrain-runtime-port]] | Porting the SecondBrain runtime components (scripts, hooks, templates) from CorporateMessenger to the Omnia project, adapting them to the new project structure and configuration. | daily/2026-05-17.md | 2026-05-19 |

| [[knowledge/concepts/zero-files-silent-failure-fix]] | Resolution of a silent failure mode where `extract_files()` returning an empty object would lead to an uninformative UI state, now providing explicit error feedback. | daily/2026-05-17.md | 2026-05-19 |

| [[knowledge/connections/haiku-integration-and-zero-files-fix-synergy]] | The integration of Claude Haiku 4.5 directly addressed the 'zero-files silent failure' by providing a model capable of generating files, while the fix ensures explicit user feedback when files are not generated, regar... | daily/2026-05-17.md | 2026-05-19 |

| [[knowledge/connections/streaming-preview-and-chat-ui-enhancements]] | The real-time streaming preview, which dynamically updates generated HTML, is complemented by the enhanced chat UI's ability to display file chips and allow non-blocking input, creating a more fluid and interactive us... | daily/2026-05-17.md | 2026-05-19 |

| [[knowledge/connections/code-view-and-snapshot-card-integration]] | The new Code View provides detailed inspection of generated files within a snapshot, while the compact and interactive snapshot cards improve navigation and discovery of these versions, making it easier to access and ... | daily/2026-05-17.md | 2026-05-19 |

| [[knowledge/concepts/gemini-integration-with-geo-block-bypass]] | Successfully integrated Google Gemini 2.5 (Pro + Flash) into the LLM Gateway, overcoming Google's geo-blocking for Russian IPs using a UK proxy and optimizing Gemini's response behavior. | daily/2026-05-19.md | 2026-05-19 |

| [[knowledge/concepts/workspace-uiux-polish]] | Implemented several UI/UX improvements in the workspace, focusing on better model selection, dynamic snapshot cards, optimized layout, and an optimistic UI update for prompt submission. | daily/2026-05-19.md | 2026-05-19 |

| [[knowledge/connections/gemini-integration-and-geo-blocking-solution-enables-new-llm-capabilities-for-users]] | The successful integration of Gemini, coupled with the geo-blocking bypass, directly expands the range of powerful LLM models available to users, fulfilling a core aspect of the platform's value proposition. | daily/2026-05-19.md | 2026-05-19 |

| [[knowledge/connections/api-stability-and-performance-directly-impacts-user-experience-and-platform-reliability]] | The fix for the API crash-loop ensures the core backend services are stable and responsive, which is critical for the smooth operation of the workspace and overall user experience, especially when interacting with LLMs. | daily/2026-05-19.md | 2026-05-19 |

| [[knowledge/connections/monorepo-structure-and-agent-briefs-guide-development-and-maintain-consistency]] | The documented monorepo structure and agent briefs (e.g., AGENT-A-FRONTEND, AGENT-B-BACKEND, AGENT-C-LLM-GATEWAY) provide clear boundaries and guidelines, ensuring that changes like Gemini integration or UI polish are... | daily/2026-05-19.md | 2026-05-19 |
