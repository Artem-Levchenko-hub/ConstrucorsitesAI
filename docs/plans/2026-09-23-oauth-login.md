# Вход через VK ID и Яндекс ID (Фаза 2 инфраструктуры, 23.09.2026)

Статус: код в `main` ждёт реквизитов приложений от владельца; без них кнопки на страницах входа
не показываются, обычный вход по email работает как раньше. Модель аккаунта не меняется:
**аккаунт — это email (+ пароль, если он есть)**. От провайдера платформа берёт только два поля —
идентификатор пользователя и email; имя, телефон, аватар и токены провайдера не сохраняются
(см. `docs/plans/2026-09-23-account-data-minimisation.md`).

## Как это работает

1. Страницы `/login`, `/register`, `/max/register` спрашивают у api `GET /api/auth/oauth/providers`
   и рисуют кнопки «Войти через VK ID» / «Войти через Яндекс ID» только для настроенных провайдеров.
2. Клик → `GET /api/auth/oauth/<vk|yandex>/start?next=…` → api кладёт `state` (и PKCE-verifier для
   VK) в таблицу `oauth_login_states` (TTL 10 минут) и отдаёт ссылку на провайдера; браузер уходит туда.
3. Провайдер возвращает браузер на `…/api/auth/oauth/<provider>/callback?code&state` (VK добавляет
   `device_id`). Api меняет код на токен, запрашивает профиль и оставляет от него только id + email.
4. Дальше три исхода:
   - аккаунт уже связан с этим провайдером **или** есть аккаунт с таким email → вход: та же
     cookie-сессия `omnia_session`, что при входе по паролю, редирект на `next` (по умолчанию `/max`);
     аккаунт, найденный по email, получает связку и `email_verified_at` (провайдер подтвердил адрес);
   - аккаунта нет → редирект на `/oauth/complete?ticket=…` — экран с email от провайдера и **теми же
     тремя обязательными согласиями**, что и при обычной регистрации (условия, политика, ПДн);
     аккаунт создаётся только после отправки формы (`POST /api/auth/oauth/complete`), без пароля,
     с подтверждённым email, личным счётом, кошельком и Free-подпиской; билет одноразовый, 15 минут;
   - отказ или сбой → `/login?oauth_error=<код>` с человеческим объяснением.

Контракт эндпоинтов — `docs/01-api-contract.md` (раздел Auth), таблицы — `docs/02-data-model.md`
(`user_identities`, `oauth_login_states`), код — `apps/api/src/omnia_api/routers/auth_oauth.py`,
`services/oauth_login.py`, web — `apps/web/src/components/auth/OAuthButtons.tsx`,
`OAuthConsentForm.tsx`, `app/(auth)/oauth/complete/page.tsx`.

## Что нужно от владельца

### Redirect URI (одинаковый принцип у обоих провайдеров)

`<OAUTH_LOGIN_REDIRECT_BASE_URL или WEB_BASE_URL>/api/auth/oauth/<provider>/callback`

| Окружение | VK ID | Яндекс ID |
|---|---|---|
| Прод (yleum.ru) | `https://yleum.ru/api/auth/oauth/vk/callback` | `https://yleum.ru/api/auth/oauth/yandex/callback` |
| Локальная разработка (web :3000, api :8000) | `http://localhost:8000/api/auth/oauth/vk/callback` | `http://localhost:8000/api/auth/oauth/yandex/callback` |

На проде `/api` проксируется nginx-ом на api, поэтому база — адрес сайта (`WEB_BASE_URL=https://yleum.ru`
в `/opt/omnia/apps/llm-gateway/deploy/full/.env`; проверить, что там не остался старый
`constructor.lead-generator.ru`). В split-origin dev задать `OAUTH_LOGIN_REDIRECT_BASE_URL=http://localhost:8000`.

### VK ID — https://id.vk.com (кабинет разработчика)

1. Создать приложение типа **Web**; базовый домен — `yleum.ru`.
2. В настройках приложения: **Доверенный redirect URL** — адрес из таблицы выше (точное совпадение,
   с `https://`); включить доступ к **email** (scope `email`), больше ничего не запрашиваем.
3. Скопировать **ID приложения** → `VK_ID_CLIENT_ID`. **Защищённый ключ** → `VK_ID_CLIENT_SECRET`
   (для самого входа не нужен — обмен кода идёт по PKCE; хранится на будущее, например для
   отзыва токенов). Сервисный ключ не нужен.
4. Приложение должно быть опубликовано (не в режиме «только тестовые пользователи»), иначе войти
   смогут только добавленные тестовые аккаунты.

Тонкости, которые стоит проверить на первом живом входе: VK ожидает `code_challenge_method=s256`
(так в их документации; мы отправляем именно это) и требует `device_id` из callback-а при обмене
кода — если VK изменит формат, вход упадёт с `oauth_exchange_failed`, а в логах api будет
`oauth login: vk exchange failed: …`.

### Яндекс ID — https://oauth.yandex.ru (создать приложение)

1. «Создать приложение» → платформа **Веб-сервисы**; Redirect URI — адрес из таблицы выше
   (можно указать и прод, и локальный).
2. Доступы (права): только **«Доступ к адресу электронной почты»** (`login:email`). Мы запрашиваем
   ровно этот scope; если право не включено, Яндекс вернёт `invalid_scope` и вход упадёт.
3. **ClientID** → `YANDEX_ID_CLIENT_ID`, **Client secret** → `YANDEX_ID_CLIENT_SECRET` (оба обязательны:
   без секрета код не обменять, кнопка не показывается).

### Куда положить значения

Прод: `/opt/omnia/apps/llm-gateway/deploy/full/.env` — значения записывать через
`infra/release/update-env-value.sh .env YANDEX_ID_CLIENT_SECRET -` (секрет со stdin, не в argv и
не в истории shell). **До 25.09.2026 compose не передавал `YANDEX_ID_*`/`VK_ID_*`/
`OAUTH_LOGIN_REDIRECT_BASE_URL` контейнеру api** (в отличие от `INTEGRATION_YANDEX_*`), и значения
в `.env` ничего не включали; теперь они доходят до одного `api`, generation-worker перекрывает их
пустыми (проверка — `infra/release/test-compose-policy.sh`). Применить: `docker compose up -d
api generation-worker` (пересоздание контейнеров; миграция `0071_oauth_login` уже применена при
первом старте api после 23.09). Локально: `apps/api/.env` (см. `apps/api/.env.example`). После
выката проверить `curl https://yleum.ru/api/auth/oauth/providers` — в ответе провайдер с заданными
реквизитами; `GET /api/auth/oauth/yandex/start` должен отвечать 302 на `https://oauth.yandex.ru/authorize`
с `redirect_uri=https://yleum.ru/api/auth/oauth/yandex/callback`, `scope=login:email` и `state`, без
секрета в URL.

### Политика привязки существующего аккаунта (принято 25.09.2026)

`_resolve_user` в `routers/auth_oauth.py`: если связки провайдера ещё нет, аккаунт с тем же email
привязывается автоматически, и адрес считается подтверждённым (`email_verified_at`). Основание:
Яндекс ID и VK ID отдают только подтверждённый ими адрес (право `login:email` / `email`), а на
платформе email — единственный идентификатор аккаунта; отдельная процедура «войти паролем и
подтвердить привязку» отложена до появления запроса. Поведение закреплено тестом
`test_existing_account_is_linked_by_verified_email_and_signed_in`; неактивный аккаунт не привязывается
и войти не может (`test_inactive_account_cannot_sign_in_through_a_provider`).

## Живая проверка (после выката и настройки)

1. `/login` → «Войти через Яндекс ID» под **новым** email → экран `/oauth/complete` показывает
   email, аккаунт не создан до отправки формы (проверить в БД: `users` пуст по этому email);
   после согласий — `/max`, в `user_identities` одна строка, `password_hash` NULL,
   `email_verified_at` заполнен, `legal_acceptances` — три записи текущей версии.
2. Тот же провайдер второй раз → сразу `/max` без экрана согласий.
3. Аккаунт, зарегистрированный по паролю с тем же email (не подтверждённый) → вход через провайдера
   связывает его и ставит `email_verified_at`; пароль продолжает работать.
4. Отмена на стороне провайдера → `/login?oauth_error=oauth_cancelled` с текстом.
5. `GET /api/account/export` → поле `identities` с одной записью (provider, provider_user_id, email).

## Допущения и риски

- Email провайдера считается подтверждённым (у Яндекса это его собственный почтовый ящик, VK
  подтверждает адрес при добавлении в профиль). Именно поэтому существующий аккаунт с тем же
  email связывается без дополнительного письма. Если владелец захочет строже — оставить связку
  только для уже подтверждённых аккаунтов.
- Если провайдер не отдал email (VK без права `email`, Яндекс-аккаунт по телефону) — вход
  отклоняется (`oauth_email_required`): без email аккаунта на платформе быть не может.
- Аккаунт, созданный через провайдера, не имеет пароля; вход по паролю для него отвечает 401,
  пароль можно задать через «Забыли пароль».
- Гонка «два callback-а одного нового человека одновременно» → второй `complete` получит 409
  `conflict` и предложит войти через провайдера ещё раз (он уже войдёт по email).
- Живой обмен кода с настоящими VK/Яндексом в тестах не воспроизводится: провайдеры подменены
  `httpx.MockTransport`; первый реальный вход — обязательный пункт проверки после выката.
