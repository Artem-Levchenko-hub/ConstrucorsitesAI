-- Изоляция данных пользователей переносится из кода в базу.
--
-- Зачем. Приложение пишет агент, и каждый новый маршрут — это новый шанс забыть
-- фильтр «только мои записи». Пока запрет живёт только в коде маршрута, одна
-- забытая строка открывает чужие заявки, согласия и переписку. После этой
-- миграции лишнего не отдаст сама база: запрос без фильтра вернёт ровно строки
-- текущего пользователя, а попытка записать строку от чужого имени упадёт.
--
-- Как приходит личность. Каждый запрос выполняется внутри транзакции, где
-- выставлен app.max_user_id (см. src/lib/db/index.ts, функция withMaxUser).
-- Если личность не выставлена, current_setting(..., true) даёт NULL, сравнение
-- не совпадает ни с одной строкой — отказ закрытый: забыли личность, значит не
-- видно НИЧЕГО, а не видно всё.
--
-- nullif(..., '') закрывает отдельную дыру: пустая строка — это значение, и без
-- него пользователь с пустым max_user_id совпал бы с незаданной личностью
-- (замечание пришло со стороны откатов при сверке схемы).
--
-- Почему FORCE. Схему проекта создаёт та же роль, под которой работает
-- приложение (CREATE SCHEMA ... AUTHORIZATION), а владелец таблицы обходит RLS.
-- Без FORCE один ENABLE был бы тихой заглушкой: политики есть, а не действуют.
--
-- Кого это не касается. Платформа читает данные проекта под суперпользователем
-- с выключенным row security (см. restoration_empty.py) — снимки, резервные
-- копии и сверка при откате видят все строки как раньше.
--
-- Таблицы без владельца намеренно остаются открытыми: max_catalog_items — это
-- витрина заведения, её показывают всем; max_webhook_events — внутренние ключи
-- защиты от повторной обработки, у них нет пользователя вовсе.

ALTER TABLE "max_users" ENABLE ROW LEVEL SECURITY;
--> statement-breakpoint
ALTER TABLE "max_users" FORCE ROW LEVEL SECURITY;
--> statement-breakpoint
DROP POLICY IF EXISTS "max_users_own_row" ON "max_users";
--> statement-breakpoint
CREATE POLICY "max_users_own_row" ON "max_users"
  USING ("max_user_id" = nullif(current_setting('app.max_user_id', true), ''))
  WITH CHECK ("max_user_id" = nullif(current_setting('app.max_user_id', true), ''));
--> statement-breakpoint

ALTER TABLE "max_business_actions" ENABLE ROW LEVEL SECURITY;
--> statement-breakpoint
ALTER TABLE "max_business_actions" FORCE ROW LEVEL SECURITY;
--> statement-breakpoint
DROP POLICY IF EXISTS "max_business_actions_own_rows" ON "max_business_actions";
--> statement-breakpoint
CREATE POLICY "max_business_actions_own_rows" ON "max_business_actions"
  USING ("max_user_id" = nullif(current_setting('app.max_user_id', true), ''))
  WITH CHECK ("max_user_id" = nullif(current_setting('app.max_user_id', true), ''));
--> statement-breakpoint

ALTER TABLE "max_consents" ENABLE ROW LEVEL SECURITY;
--> statement-breakpoint
ALTER TABLE "max_consents" FORCE ROW LEVEL SECURITY;
--> statement-breakpoint
DROP POLICY IF EXISTS "max_consents_own_rows" ON "max_consents";
--> statement-breakpoint
CREATE POLICY "max_consents_own_rows" ON "max_consents"
  USING ("max_user_id" = nullif(current_setting('app.max_user_id', true), ''))
  WITH CHECK ("max_user_id" = nullif(current_setting('app.max_user_id', true), ''));
--> statement-breakpoint

ALTER TABLE "max_analytics_events" ENABLE ROW LEVEL SECURITY;
--> statement-breakpoint
ALTER TABLE "max_analytics_events" FORCE ROW LEVEL SECURITY;
--> statement-breakpoint
DROP POLICY IF EXISTS "max_analytics_events_own_rows" ON "max_analytics_events";
--> statement-breakpoint
CREATE POLICY "max_analytics_events_own_rows" ON "max_analytics_events"
  USING ("max_user_id" = nullif(current_setting('app.max_user_id', true), ''))
  WITH CHECK ("max_user_id" = nullif(current_setting('app.max_user_id', true), ''));
--> statement-breakpoint

ALTER TABLE "max_bot_outbox" ENABLE ROW LEVEL SECURITY;
--> statement-breakpoint
ALTER TABLE "max_bot_outbox" FORCE ROW LEVEL SECURITY;
--> statement-breakpoint
DROP POLICY IF EXISTS "max_bot_outbox_own_rows" ON "max_bot_outbox";
--> statement-breakpoint
CREATE POLICY "max_bot_outbox_own_rows" ON "max_bot_outbox"
  USING ("max_user_id" = nullif(current_setting('app.max_user_id', true), ''))
  WITH CHECK ("max_user_id" = nullif(current_setting('app.max_user_id', true), ''));
--> statement-breakpoint

ALTER TABLE "max_audit_log" ENABLE ROW LEVEL SECURITY;
--> statement-breakpoint
ALTER TABLE "max_audit_log" FORCE ROW LEVEL SECURITY;
--> statement-breakpoint
DROP POLICY IF EXISTS "max_audit_log_own_rows" ON "max_audit_log";
--> statement-breakpoint
CREATE POLICY "max_audit_log_own_rows" ON "max_audit_log"
  USING ("max_user_id" = nullif(current_setting('app.max_user_id', true), ''))
  WITH CHECK ("max_user_id" = nullif(current_setting('app.max_user_id', true), ''));
