# apps/landing — landing.omniadevelop.ru snapshot

Снапшот production-лендинга с VPS + nginx-конфиги. Не билдится из этой папки — это **слепок** того, что лежит на сервере, для версионирования и быстрого rollback.

## Структура

```
apps/landing/
├── dist/                                — статика, как на сервере в /var/www/landing-omniadevelop/
│   ├── index.html                       — корневой HTML (React SPA shell)
│   ├── app.js                           — бандл лендинга
│   └── _omnia-cta.js                    — наш CTA-перехватчик (см. ниже)
└── nginx/
    ├── landing.omniadevelop.ru.conf     — production vhost (включает sub_filter)
    └── app.omniadevelop.ru.template     — заготовка vhost для будущего SSO (см. ниже)
```

## CTA handoff: landing → конструктор

Лендинг — React SPA, исходники владельца перевыкатываются отдельно (новый bundle hash = новый `app.js?v=…`). Чтобы клик по «Начать бесплатно» / «Войти» уводил пользователя на конструктор `https://constructor.lead-generator.ru/register?next=/projects`, мы делаем две вещи:

1. **`_omnia-cta.js`** — клиентский click-перехватчик, использует event delegation на `document` capture. Регулярки матчат рендеренный `textContent` кнопок:
   - `Начать бесплатно | Создать сайт | Попробовать | Начать сейчас` → `/register?next=/projects`
   - `Войти | Авторизоваться | Вход` → `/login?next=/projects`

2. **Nginx `sub_filter`** в `landing.omniadevelop.ru.conf` (HTTPS server-блок, после `add_header X-Frame-Options`):
   ```nginx
   sub_filter_once on;
   sub_filter "</body>" "<script src=/_omnia-cta.js?v=2 defer></script></body>";
   ```
   Это inject'ит `<script src="/_omnia-cta.js">` в каждый HTML-ответ — переживает любой redeploy `index.html`.

## Обновление лендинга

Когда владелец перекатывает свежий билд (`app.js?v=…` меняется):

```bash
# Pull свежую версию с сервера
scp -r kanavto-vps:/var/www/landing-omniadevelop/{index.html,app.js,_omnia-cta.js} apps/landing/dist/
git add apps/landing/dist/ && git commit -m "chore(landing): sync vN.x from VPS"
```

Если CTA-кнопки получили новый текст — обновить регулярки в `apps/landing/dist/_omnia-cta.js` + `scp` обратно на сервер в `/var/www/landing-omniadevelop/_omnia-cta.js` (требует `sudo`).

## SSO на `app.omniadevelop.ru` (future)

Шаблон vhost — `nginx/app.omniadevelop.ru.template`. Активация описана в шапке самого шаблона: DNS A-record → certbot → cp → env switch на стороне `apps/llm-gateway/deploy/full/.env` + `apps/web` rebuild → `_omnia-cta.js` BASE flip.

Сейчас (2026-05-25) handoff работает на `constructor.lead-generator.ru` без cookie-SSO (cookie scoped на тот хост; пользователь логинится один раз в форме регистрации). После переезда на `.omniadevelop.ru` cookie станет parent-domain → SSO.
