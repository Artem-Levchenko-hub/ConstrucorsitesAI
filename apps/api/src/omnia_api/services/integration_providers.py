"""Provider registry and live credential checks for Integration Hub."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx


class IntegrationProviderError(RuntimeError):
    """A safe provider error suitable for returning to the owner."""


class IntegrationCredentialsInvalid(IntegrationProviderError):
    pass


class IntegrationProviderUnavailable(IntegrationProviderError):
    pass


@dataclass(frozen=True)
class IntegrationField:
    key: str
    label: str
    placeholder: str = ""
    help: str = ""
    secret: bool = False
    required: bool = True


@dataclass(frozen=True)
class IntegrationProvider:
    key: str
    name: str
    category: str
    description: str
    capabilities: tuple[str, ...]
    fields: tuple[IntegrationField, ...]
    available: bool
    docs_url: str
    recommended: bool = False
    requirement: str | None = None
    oauth_supported: bool = False


PROVIDERS: tuple[IntegrationProvider, ...] = (
    IntegrationProvider(
        key="yookassa",
        name="ЮKassa",
        category="payments",
        description="Приём оплаты, возвраты и подтверждение статусов платежей.",
        capabilities=("Оплата", "Возвраты", "Статусы", "Чеки"),
        fields=(
            IntegrationField(
                "shop_id",
                "shopId",
                "123456",
                "Идентификатор магазина из кабинета ЮKassa.",
            ),
            IntegrationField(
                "secret_key",
                "Секретный ключ",
                "live_••••••••",
                "Ключ API магазина. Он будет зашифрован и больше не показывается.",
                secret=True,
            ),
        ),
        available=True,
        docs_url="https://yookassa.ru/developers/api",
        recommended=True,
        oauth_supported=True,
    ),
    IntegrationProvider(
        key="iiko",
        name="iikoCloud",
        category="restaurant",
        description="Меню, организации и ресторанные заказы через iikoCloud API.",
        capabilities=("Меню", "Стоп-лист", "Заказы", "Организации"),
        fields=(
            IntegrationField(
                "api_login",
                "API login",
                "Вставьте ключ iikoCloud",
                "Ключ доступа из кабинета iiko.",
                secret=True,
            ),
        ),
        available=True,
        docs_url="https://api-ru.iiko.services/",
        recommended=True,
    ),
    IntegrationProvider(
        key="bitrix24",
        name="Битрикс24",
        category="crm",
        description="Передача заявок, клиентов и сделок во входящий webhook.",
        capabilities=("Лиды", "Контакты", "Сделки", "Задачи"),
        fields=(
            IntegrationField(
                "webhook_url",
                "URL входящего webhook",
                "https://company.bitrix24.ru/rest/1/••••••/",
                "Создайте входящий webhook с нужными правами в Битрикс24.",
                secret=True,
            ),
        ),
        available=True,
        docs_url="https://apidocs.bitrix24.ru/",
        oauth_supported=True,
    ),
    IntegrationProvider(
        key="moysklad",
        name="МойСклад",
        category="inventory",
        description="Товары, цены, остатки и заказы покупателей.",
        capabilities=("Товары", "Остатки", "Цены", "Заказы"),
        fields=(
            IntegrationField(
                "token",
                "Токен доступа",
                "Вставьте токен МойСклад",
                "Токен API пользователя с минимально необходимыми правами.",
                secret=True,
            ),
        ),
        available=True,
        docs_url="https://dev.moysklad.ru/doc/api/remap/1.2/",
    ),
    IntegrationProvider(
        key="yandex_metrica",
        name="Яндекс Метрика",
        category="analytics",
        description="События, цели и измерение конверсий мини-приложения.",
        capabilities=("События", "Цели", "Конверсии", "Отчёты"),
        fields=(
            IntegrationField(
                "counter_id",
                "Номер счётчика",
                "12345678",
                "Идентификатор счётчика Метрики.",
            ),
            IntegrationField(
                "oauth_token",
                "OAuth-токен",
                "y0_AgAAAA••••••",
                "Токен с доступом на чтение счётчика.",
                secret=True,
            ),
        ),
        available=True,
        docs_url="https://yandex.ru/dev/metrika/",
        oauth_supported=True,
    ),
    IntegrationProvider(
        key="rkeeper",
        name="r_keeper",
        category="restaurant",
        description="Меню и заказы для ресторанов с r_keeper Delivery или r_keeper 7.",
        capabilities=("Меню", "Стоп-лист", "Заказы", "Оплата"),
        fields=(),
        available=False,
        requirement="Нужны лицензия Delivery_Api и доступный API либо локальный коннектор.",
        docs_url="https://docs.rkeeper.ru/delivery/podklyuchenie-po-api-4043842.html",
        recommended=True,
    ),
    IntegrationProvider(
        key="yclients",
        name="YCLIENTS",
        category="booking",
        description="Услуги, сотрудники, свободные слоты и онлайн-запись.",
        capabilities=("Услуги", "Сотрудники", "Расписание", "Записи"),
        fields=(),
        available=False,
        requirement="Нужен партнёрский доступ к API и OAuth-сценарий подключения.",
        docs_url="https://developers.yclients.com/",
    ),
    IntegrationProvider(
        key="amocrm",
        name="amoCRM",
        category="crm",
        description="Лиды, контакты, сделки и автоматические задачи.",
        capabilities=("Лиды", "Контакты", "Сделки", "Задачи"),
        fields=(),
        available=False,
        requirement="Нужно зарегистрировать интеграцию amoCRM и настроить OAuth callback.",
        docs_url="https://www.amocrm.ru/developers/content/oauth/step-by-step",
        oauth_supported=True,
    ),
    IntegrationProvider(
        key="one_c",
        name="1С",
        category="inventory",
        description="Каталог, цены, остатки, клиенты и документы.",
        capabilities=("Товары", "Остатки", "Цены", "Документы"),
        fields=(),
        available=False,
        requirement="Нужна публикация OData/HTTP-сервиса конкретной конфигурации 1С.",
        docs_url="https://its.1c.ru/db/v8318doc#bookmark:dev:TI000001265",
    ),
    IntegrationProvider(
        key="cdek",
        name="CDEK",
        category="delivery",
        description="Расчёт доставки, пункты выдачи, оформление и статусы.",
        capabilities=("Тарифы", "ПВЗ", "Отправления", "Статусы"),
        fields=(),
        available=False,
        requirement="Нужны договор CDEK и OAuth-ключи клиента.",
        docs_url="https://apidoc.cdek.ru/",
    ),
)

PROVIDER_MAP = {provider.key: provider for provider in PROVIDERS}


def get_provider(provider_key: str) -> IntegrationProvider:
    try:
        return PROVIDER_MAP[provider_key]
    except KeyError as exc:
        raise IntegrationProviderError("Неизвестная интеграция") from exc


def split_values(
    provider: IntegrationProvider, values: dict[str, str]
) -> tuple[dict[str, str], dict[str, str]]:
    expected = {field.key: field for field in provider.fields}
    unknown = set(values) - set(expected)
    if unknown:
        raise IntegrationProviderError("Переданы неизвестные поля интеграции")
    missing = [
        field.label
        for field in provider.fields
        if field.required and not values.get(field.key, "").strip()
    ]
    if missing:
        raise IntegrationProviderError(f"Заполните обязательные поля: {', '.join(missing)}")
    public_values: dict[str, str] = {}
    secret_values: dict[str, str] = {}
    for key, field in expected.items():
        value = values.get(key, "").strip()
        if not value:
            continue
        (secret_values if field.secret else public_values)[key] = value
    return public_values, secret_values


def _provider_http_error(provider_name: str, response: httpx.Response) -> None:
    if response.status_code in {400, 401, 403, 404}:
        raise IntegrationCredentialsInvalid(
            f"{provider_name} отклонил реквизиты. Проверьте значение и права доступа."
        )
    if response.status_code == 429:
        raise IntegrationProviderUnavailable(
            f"{provider_name} временно ограничил частоту запросов. Повторите проверку позже."
        )
    if response.status_code >= 500:
        raise IntegrationProviderUnavailable(
            f"{provider_name} временно недоступен. Подключение сохранено не было."
        )
    if response.status_code >= 300:
        raise IntegrationProviderError(
            f"{provider_name} вернул неожиданный ответ HTTP {response.status_code}."
        )


def _bitrix_profile_url(value: str) -> tuple[str, str]:
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").lower()
    allowed = (
        ".bitrix24.ru",
        ".bitrix24.com",
        ".bitrix24.kz",
        ".bitrix24.by",
        ".bitrix24.eu",
    )
    if parsed.scheme != "https" or not any(host.endswith(suffix) for suffix in allowed):
        raise IntegrationCredentialsInvalid(
            "Используйте HTTPS-адрес входящего webhook облачного Битрикс24."
        )
    path = parsed.path.rstrip("/")
    parts = [part for part in path.split("/") if part]
    if len(parts) < 3 or parts[0] != "rest":
        raise IntegrationCredentialsInvalid(
            "URL должен иметь вид https://portal.bitrix24.ru/rest/USER_ID/SECRET/"
        )
    return f"https://{host}/{path.lstrip('/')}/profile.json", host


async def verify_provider(
    provider_key: str,
    public_values: dict[str, str],
    secret_values: dict[str, str],
) -> str:
    """Verify credentials against a read-only provider endpoint."""
    provider = get_provider(provider_key)
    is_oauth = bool(secret_values.get("access_token"))
    if not provider.available and not (provider.oauth_supported and is_oauth):
        raise IntegrationProviderError(provider.requirement or "Интеграция пока недоступна")

    headers = {"User-Agent": "Omnia-Integration-Hub/1.0", "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(12.0),
            follow_redirects=False,
            headers=headers,
        ) as client:
            if provider_key == "yookassa":
                if is_oauth:
                    response = await client.get(
                        "https://api.yookassa.ru/v3/me",
                        headers={
                            **headers,
                            "Authorization": f"Bearer {secret_values['access_token']}",
                        },
                    )
                    _provider_http_error(provider.name, response)
                    yookassa_payload = response.json()
                    account_id = yookassa_payload.get(
                        "account_id"
                    ) or public_values.get("shop_id")
                    return f"Магазин {account_id}" if account_id else "ЮKassa"
                response = await client.get(
                    "https://api.yookassa.ru/v3/payments",
                    params={"limit": 1},
                    auth=(public_values["shop_id"], secret_values["secret_key"]),
                )
                _provider_http_error(provider.name, response)
                return f"Магазин {public_values['shop_id']}"

            if provider_key == "iiko":
                response = await client.post(
                    "https://api-ru.iiko.services/api/1/access_token",
                    json={"apiLogin": secret_values["api_login"]},
                )
                _provider_http_error(provider.name, response)
                payload: Any = response.json()
                token = payload.get("token") if isinstance(payload, dict) else payload
                if not isinstance(token, str) or not token:
                    raise IntegrationProviderError("iikoCloud не вернул токен доступа.")
                return "iikoCloud API"

            if provider_key == "bitrix24":
                if is_oauth:
                    endpoint = public_values.get("client_endpoint", "").rstrip("/")
                    parsed = urlparse(endpoint)
                    if parsed.scheme != "https" or not parsed.hostname:
                        raise IntegrationCredentialsInvalid(
                            "Сохранённый адрес портала Битрикс24 некорректен."
                        )
                    response = await client.get(
                        f"{endpoint}/profile.json",
                        params={"auth": secret_values["access_token"]},
                    )
                    _provider_http_error(provider.name, response)
                    payload = response.json()
                    result = payload.get("result", {}) if isinstance(payload, dict) else {}
                    display = result.get("NAME") or result.get("ID") or parsed.hostname
                    return f"{parsed.hostname} · {display}"
                profile_url, host = _bitrix_profile_url(secret_values["webhook_url"])
                response = await client.get(profile_url)
                _provider_http_error(provider.name, response)
                payload = response.json()
                result = payload.get("result", {}) if isinstance(payload, dict) else {}
                display = (
                    result.get("NAME")
                    or result.get("LAST_NAME")
                    or result.get("ID")
                    or host
                )
                return f"{host} · {display}"

            if provider_key == "moysklad":
                response = await client.get(
                    "https://api.moysklad.ru/api/remap/1.2/context/companysettings",
                    headers={
                        **headers,
                        "Authorization": f"Bearer {secret_values['token']}",
                    },
                )
                _provider_http_error(provider.name, response)
                payload = response.json()
                company = payload.get("name") if isinstance(payload, dict) else None
                return str(company or "Аккаунт МойСклад")

            if provider_key == "yandex_metrica":
                counter_id = public_values["counter_id"]
                if not counter_id.isdigit():
                    raise IntegrationCredentialsInvalid("Номер счётчика должен состоять из цифр.")
                response = await client.get(
                    f"https://api-metrika.yandex.net/management/v1/counter/{counter_id}",
                    headers={
                        **headers,
                        "Authorization": f"OAuth {secret_values['oauth_token']}",
                    },
                )
                _provider_http_error(provider.name, response)
                payload = response.json()
                counter = payload.get("counter", {}) if isinstance(payload, dict) else {}
                return str(counter.get("name") or f"Счётчик {counter_id}")

            if provider_key == "amocrm" and is_oauth:
                base_url = public_values.get("base_url", "").rstrip("/")
                parsed = urlparse(base_url)
                if (
                    parsed.scheme != "https"
                    or not parsed.hostname
                    or not parsed.hostname.endswith((".amocrm.ru", ".kommo.com"))
                ):
                    raise IntegrationCredentialsInvalid(
                        "Сохранённый адрес аккаунта amoCRM некорректен."
                    )
                response = await client.get(
                    f"{base_url}/api/v4/account",
                    headers={
                        **headers,
                        "Authorization": f"Bearer {secret_values['access_token']}",
                    },
                )
                _provider_http_error(provider.name, response)
                payload = response.json()
                return str(payload.get("name") or parsed.hostname)
    except IntegrationProviderError:
        raise
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise IntegrationProviderUnavailable(
            f"Не удалось связаться с {provider.name}. Повторите проверку позже."
        ) from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise IntegrationProviderError(
            f"{provider.name} вернул ответ в неизвестном формате."
        ) from exc

    raise IntegrationProviderError("Для этой интеграции ещё нет проверки доступа.")
