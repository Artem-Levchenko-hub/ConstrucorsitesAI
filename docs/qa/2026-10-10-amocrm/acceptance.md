# amoCRM: техническая приёмка и границы доказательств

10 октября 2026. Public client ID: `1c5f6755-da59-4d78-b7c6-7c0527fb9c80`.
Redirect: `https://yleum.ru/api/integrations/oauth/amocrm/callback`.

## Проверено локально

- API:119 целевых тестов, включая реальный PostgreSQL concurrency; migration15; Ruff; mypy Linux282 файлов.
- Web:1023 tests, TypeScript, lint0errors (одно существующее apple-icon warning). OAuth prototype/duplicate regression: RED→GREEN.
- Production compose safety:4 passed. Независимое review: No findings после исправления P2.
- Полный API pytest/CI и delivery фиксируются отдельным release receipt после завершения, а не этим списком.

## Не считать подтверждённым

| Сценарий | Требуемое доказательство |
|---|---|
| Новый public OAuth | Реальный новый владелец, URL public client ID, callback, выбранный аккаунт, безопасный receipt |
| A/B | Два независимых Yleum/amoCRM владельца; свои positive controls; чужие GET/изменение/удаление в обе стороны |
| Одна заявка | Doubleclick + retry того же operation key после потери ответа; одна внешняя сделка |
| История | CRM stage change → ручной refresh MAX → reopen/reentry; собственные данные сохраняются |
| Legacy | Refresh исходным legacy profile после rollout, без изменения другого владельца |
| Отзыв/переподключение | Изолированный A; B сохраняет доступ и историю; общий reconnect flow отдельно утверждён |
| Демо модератора | Точный проект/бот/link, разрешённый минимальный доступ, неизменяемый сценарий |
| Тарифы | Проверка обычным production пользователем; catalog flag не заменяет пользовательский путь |

Внешние шаги заблокированы до отдельного согласования новых credentials/grants и синтетических записей. Второй существующий MAX-бот при проверке находился на модерации; активный Coffee не переносим. Проверенный ранее Coffee-flow не является доказательством текущего isolated public OAuth demo.

## Данные и согласие

Действующий API передаёт имя, контакт при наличии, комментарий, источник, project ID и MAX ID в примечании. История ограничена project/actor/connection/CRM account, не разбирает MAX ID из примечания. Внешние автоматизации клиента неизвестны: поле не удалено.

Consent receipt сохраняет policyVersion/решение/actor/time, не полный текст/hash. Отдельного серверного hard gate перед CRM lead сейчас нет: конкретная generated form обязана проверить согласие и записать receipt; отказ без согласия требует живого теста. Юридические роли/сроки не выдуманы и должны быть утверждены до публикации черновиков.

## Recovery и доставка

Production — documented full + hostdb; public OAuth disabled, legacy pair unchanged. После additive0082 при healthfailure сохранять drain/lock и делать совместимый forward-fix, не автоматический downgrade или полный DBrestore. Старые IDs/owners/bindings сохраняются; допустимый token refresh сам по себе не нарушение сохранности.
