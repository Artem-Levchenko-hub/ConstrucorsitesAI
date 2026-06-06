"""Art-Director → Writer 2-pass generator for FREEFORM HTML (owner directive
2026-06-01).

The fixed build orchestration. Splits one generation across two models so the
design intelligence runs on a strong model while the bulk HTML tokens run on a
cheap one:

* **Art-Director** (role ``art_director`` → Opus) — the *вдохновитель*. Sees the
  full freeform system prompt (palette anchor, kit, taste codex) + the user
  brief and emits an ULTRA-DETAILED design brief: the РАЗБОР (feeling → idea →
  reference → system) plus a per-section spec — exact palette HEX, fonts, motion
  signature, layout, real Russian copy, which kit classes/effects, and the exact
  ``data-omnia-gen`` image prompts. It writes NO code, so its expensive tokens
  stay few.

* **Writer** (role ``freeform_writer`` → DeepSeek) — executes the brief
  literally into the full HTML and streams it to the caller. The brief carries
  every design decision, so the cheap model only has to realise it, not invent
  it. This is the bulk-token pass — hence the cheap model.

Net latency ≈ Art-Director (short output) + Writer (full page). Token spend is
dominated by the Writer's cheap output; the Opus pass is a small brief.

The async-generator event contract matches
``services.llm_client.stream_chat_completion`` so the caller treats it
identically:
* ``{"delta": str}`` — chunks of the FINAL (Writer) HTML
* ``{"usage": dict}`` — summed tokens / cost across both passes
* ``{"error": str}`` — terminal failure
* ``{"pass": "art_director|writer", "stage": "start|end"}`` — progress

Fail-soft (R-10): if the Art-Director returns an empty brief, the Writer still
runs on the base freeform prompt alone — a page without the brief beats no page.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from omnia_api.core.config import model_for_role
from omnia_api.services import pipeline_debug
from omnia_api.services.llm_client import stream_chat_completion
from omnia_api.services.vendor_profiles import vendor_directive


# Pass 1 — the art-director writes a brief, never code. Appended to the last
# user turn so the shared system prompt (with the palette anchor + kit + taste
# codex) stays byte-identical across both passes → Anthropic prompt-cache hit.
_ART_DIRECTOR_INSTRUCTION = """\
Ты — АРТ-ДИРЕКТОР и автор СПЕЦИФИКАЦИИ (проход 1 из 2). Ты НЕ пишешь код. Ты пишешь
НАСТОЛЬКО подробный бриф, что верстальщик (проход 2) физически не сможет сделать
дёшево или ошибиться: все решения уже приняты ТОБОЙ, ему остаётся ТОЛЬКО перенести
твой текст в HTML. ЖЕЛЕЗНОЕ ПРАВИЛО: чего нет в брифе — верстальщик выдумает и
испортит. Значит, не оставляй НИ ОДНОГО пробела для импровизации — ни цвета, ни
слова, ни класса, ни отступа.

Думай как сеньор-арт-директор: сначала чувство, потом система, потом посекционная
спека с ГОТОВЫМ дословным контентом и ТОЧНЫМИ классами. Палитру и шрифты бери ТОЧНО
из обязательного блока системного промпта выше (те же HEX, акцент — дозой).
Запрещённые training-дефолты indigo/violet — не предлагай.

Формат — СТРОГО так, плотно, без воды, без markdown-ограждений вокруг ответа:

# 1. РАЗБОР (3 строки)
ЧУВСТВО: <что ощущает человек за первую секунду>
ИДЕЯ: <единственная организующая мысль, которую он запомнит>
РЕФЕРЕНС: <«как X встретил Y» — вайб-якорь, не копия чужого сайта>

# 2. ГЛОБАЛ (точные токены — верстальщик ставит их 1:1)
ФОН <#HEX> · ТЕКСТ <#HEX> · PRIMARY <#HEX> · АКЦЕНТ <#HEX> (дозой: только CTA+цифры, НЕ заливка блоков)
АНТИ-ДЁШЕВО (ЖЕЛЕЗНО — это главный анти-AI-фильтр): МАКСИМУМ 2 хью — доминанта (тёмная/нейтраль) + ОДИН акцент дозой. ЗАПРЕЩЕНЫ радужные/многоцветные градиенты (teal→violet→red = дёшево, мгновенно читается как AI). Любой градиент / .omnia-shader / .bg-mesh — ТОЛЬКО ТОН-В-ТОН: data-omnia-colors = 3-4 оттенка ОДНОЙ хью-семьи, низкая насыщенность, плавно. ЗАПРЕЩЕНЫ glow / неоновые подсветки / цветные ореолы под кнопками и блоками — глубина ТОЛЬКО мягкой тенью с подтоном (.depth-2|3), НЕ свечением. Кнопки: сплошная заливка + аккуратная тень, БЕЗ halo. Дорого = сдержанно: фото + фактура + один акцент, а не «больше цветов и свечения».
ШРИФТЫ: дисплей "<точное имя>" · текст "<точное имя>"
MOTION-СИГНАТУРА: ОДНА — <kit-эффект и в какой секции: .omnia-shader data-omnia-colors="#..,#..,#..,#.." (ТОН-В-ТОН, одна хью-семья) | .line-rise | .omnia-spotlight | scramble> (reduced-motion-safe)
РИТМ: отступы секций <py-24|py-32>, контейнер <max-w-6xl|7xl>, радиус <rounded-xl|2xl>, 8pt-сетка
ФАКТУРА/ГЛУБИНА: где .grain / .depth-2|3 / градиент тон-в-тон
ХЕДЕР — ЗАПРЕЩЁН generic-AI-дефолт #1: «лого слева + горизонт-меню по центру + кнопка справа» (мгновенно читается как шаблон). ОБЯЗАН быть характерным. Выбери ОДИН архетип под вайб (НЕ дефолт, и НЕ тот же из билда в билд) и опиши точно (фон, скролл-поведение, типографику пунктов, где CTA, граница/тень):
  1) Вертикальный боковой рельс-нав (фикс слева/справа, лого сверху, пункты в столбик).
  2) Гигантский лого-вордмарк во всю ширину + мелкая нав под ним.
  3) Сплит-хедер: лого слева, нав справа, центр ПУСТ (воздух).
  4) Центр-лого, навигация разнесена по бокам от него.
  5) Тикер-бар сверху (анонс/бегущая строка) + тонкая нав под ним.
  6) Всегда off-canvas: только лого + бургер даже на десктопе (контент-фокус).
  7) Pill-навигация: капсула .rounded-full со стекло-фоном под пунктами.
  8) Ужимающийся: на скролле сворачивается в компактную плавающую пилюлю.

# 2.5 АРХИТЕКТУРА — ВЫВЕДИ ИЗ БИЗНЕСА (это и есть главный анти-шаблон)
Прежде чем расписывать секции, прими 4 решения и КОРОТКО зафиксируй их перед списком:
ЦЕЛЬ: одно действие, ради которого существует страница (заявка / звонок / покупка / бронь / подписка).
ПОСЕТИТЕЛЬ: кто приходит и что ему нужно понять и проверить, чтобы решиться.
ГЕРОЙ СМЫСЛА: какая секция — главная (у студии — работы, у SaaS — продукт в деле, у мастера — результат, у события — программа и дата, у товара — сам товар).
СОБСТВЕННЫЙ КАРКАС: выпиши список секций ИМЕННО под этот бизнес, в осмысленном порядке. НЕ бери механически «обязательный набор» (доверие→фичи→как-работает→отзывы→тарифы→FAQ) — включай блок ТОЛЬКО если он реально нужен этому бизнесу, и добавляй нетиповые секции, которых требует ниша (меню, расписание, кейс, калькулятор цены, карта-проезд, лента-портфолио, манифест, состав/анатомия продукта…). Два разных бизнеса ОБЯЗАНЫ получить два разных каркаса.

# 3. СЕКЦИИ — твой каркас из 2.5, по порядку (столько секций, сколько требует история бизнеса — без добивки до числа и без выкидывания нужного), КАЖДАЯ со ВСЕМИ полями:
[N] <назначение> | id="<anchor>"
ФОН/ОТСТУП/КОНТЕЙНЕР: <#HEX или kit-класс> · py-<N> · max-w-<N>
РАСКЛАДКА: <точно: число колонок, выравнивание, асимметрия; для hero — что на первом экране>
КОПИРАЙТ (ДОСЛОВНО, готовый — верстальщик копирует как есть, в кавычках):
  Eyebrow: "<...>" · Заголовок: "<...>" · Подзаголовок: "<...>"
  Карточки/булиты: "<каждый пункт ЦЕЛИКОМ>" (а не «три преимущества»)
  Кнопки: "<лейбл>" → href="<#якорь|tel:|mailto:>"
  Цифры/цены в ₽, имена людей, город, адрес, телефон — РЕАЛЬНЫЕ и правдоподобные
KIT-КЛАССЫ: <точные классы на ключевые элементы: .btn-cta-primary, .reveal|.line-rise, .depth-2|3, .bento, .glass-dark ...>
ВИЗУАЛ (КАЖДАЯ секция несёт ≥1 из 3 инструментов; плоская заливка/один линейный градиент без визуала = брак. Комбинируй, НЕ только фото. Для каждой секции в спеке укажи строкой «ВИЗУАЛ: <А/Б/В + точные классы/мотив>»):
  (А) ПРОСТАЯ АБСТРАКТНАЯ ГЕОМЕТРИЯ (НЕ рисуй предметы руками). СТРОГО ЗАПРЕЩЕНО придумывать инлайн-<svg> с предметами/едой/существами/лицами/маскотами/«рыба одной линией»/«палочки»/«ролл» — у моделей они выходят кривыми и читаются как уродливые рожи (это БРАК, главная причина «стрёмных лиц»). Любое изображение предмета/блюда/сцены/человека — ТОЛЬКО фото из (В), НИКОГДА не вектор руками. Разрешён лишь МИНИМАЛЬНЫЙ абстрактный вектор-декор: одна-две тонкие линии или дуга-разделитель, концентрические круги, сетка точек/линий, мягкая волна-разделитель между секциями (line-art, тон-в-тон, аккуратный stroke, без «фигур»). Если форма хоть отдалённо похожа на объект или лицо — НЕ ставь её, бери фото (В) или классы ГРАФ-АРСЕНАЛА (В).
  (Б) ТИПОГРАФИКА-КАК-ГРАФИКА — визуал секции из ОДНОЙ акцентной типографики: .display-fill (тип во весь экран) · вордмарк во всю ширину · постерный МИКС заливки и КОНТУРА по словам (.text-stroke / .text-stroke-2, style="--stroke:#hex") · акцент-слово .gradient-text/.text-shimmer · .split-chars/.line-rise/data-anime="hero-stagger" (кинетик-вход) · цифра-герой · тип, уезжающий за край · .text-blend (врезка заголовка в фото).
  (В) ГЕНЕРАТИВНЫЙ ФОН / ФОТО-СЛОЙ — ГРАФ-АРСЕНАЛ кита (первым ребёнком в relative overflow-hidden, контент в .omnia-shader-over): .omnia-shader (WebGL-атмосфера, data-omnia-colors тон-в-тон) · .fx-aurora-soft (тон-в-тон аврора) · .fx-beams (лучи) · .fx-meteors (метеоры, дозой) · .fx-grid-glow · .fx-waves (органик/фуд) · .blob/.orb (морф-сферы тон-в-тон) · .bg-mesh/.gradient-soft-mesh · .line-grid/.dot-grid · .grain/.film-grain (фактура) · .fx-trace (луч по бордеру одного CTA). ЕСЛИ фото (<img data-omnia-gen="<детальный EN-prompt: предмет, сцена, свет, ракурс, объектив; без текста/логотипов>"> full-bleed + overlay контраст ≥4.5:1) — ПОВЕРХ ОБЯЗАТЕЛЕН граф-слой/деталь (SVG-линии, рамка, .grain, mesh), НЕ голое плоское фото.
АДАПТИВ: <что складывается в столбец на мобильном, что прячется, как мельчает заголовок>

# 4. КРАФТ-ФЛОР (явный запрет дешевизны — повтори для верстальщика)
— ЗАПРЕЩЕНО generic-AI: центрованный текст + три одинаковые карточки + одна кнопка + пустой/градиентный фон ВМЕСТО тематической графики.
— ВЁРСТКА РАЗНООБРАЗНА: соседние секции УЗНАВАЕМО разные по раскладке — чередуй full-bleed band, асимметричный сплит, overlap/слои, bento-сетку, гигантский тип, цитату во весь экран. НИКОГДА не 3-4 одинаковые секции-карточки подряд.
— ТЕМАТИЧЕСКИЕ ФОТО ОБЯЗАТЕЛЬНЫ: hero + ≥2 ключевые секции несут НАСТОЯЩЕЕ фото-фон или фото-объект (<img data-omnia-gen=...>), а не чёрный фон с бледным вектором. Страница «всё-чёрная заливка + еле видный SVG» = брак (выглядит пусто). НЕ рисуй фигуративные SVG руками (предметы/лица/маскоты выходят кривыми) — предметы только фото, вектор только простой абстрактный (см. (А)).
— ГЛАВНЫЙ ЭКРАН (hero) = ГЛАВНЫЙ АКЦЕНТ страницы (детали — в блоке «ГЕРОЙ — ГЛАВНЫЙ
  АКЦЕНТ» системного промпта). Выбери и ЯВНО зафиксируй РОВНО ОДИН режим, доведи до
  предела. Вялый мелкий заголовок по центру на плоском/градиентном фоне = БРАК:
  • РЕЖИМ I — ТИП-ГЕРОЙ: заголовок САМ графика (фото не нужно) — гигантский .display-fill,
    постерный микс заливки и КОНТУРА .text-stroke по словам, одно акцент-слово
    .gradient-text/.text-shimmer, ОДИН кинетик-вход (data-anime="hero-stagger" | .line-rise
    | data-omnia-scramble), посаженный на ЖИВОЙ тон-в-тон фон (.omnia-shader
    data-omnia-colors="#..,#..,#..,#.." / .fx-aurora-soft / .bg-mesh) — НЕ плоская заливка.
  • РЕЖИМ II — ФОТО/АРТ-ГЕРОЙ: full-bleed <img data-omnia-gen="..."> absolute inset-0
    object-cover (ФОТО или НАРИСОВАННАЯ графика — укажи стиль в промпте: editorial
    illustration / painterly / risograph / isometric 3D / matte painting) + УМЕРЕННОЕ
    затемнение `/45-/55` (НИКОГДА `/70`…`/90` и не сплошная чернота) + граф-слой поверх
    (.grain/.fx-grid-glow/линия-рамка) + тон-грейд (.tone-warm/.tone-cool/.tone-monochrome).
    Текст защищай ЛОКАЛЬНО (.text-protect / градиент только под текстом, НЕ чернота на весь
    экран); заголовок можно врезать в кадр через .text-blend.
  Зафиксируй в брифе строкой: ГЕРОЙ-РЕЖИМ: I|II + точные классы.
— ГРАФ-СЛОЙ В КАЖДОЙ СЕКЦИИ — ≥1 из: ГРАФ-АРСЕНАЛ (.omnia-shader/.fx-aurora-soft/.fx-beams/.fx-meteors/.fx-grid-glow/.fx-waves/.blob/.orb/.bg-mesh/.grain/.line-grid/.dot-grid/.fx-trace) ИЛИ простой абстрактный вектор-разделитель ИЛИ типографика-как-графика. Где фото — граф-слой ПОВЕРХ. Плоская секция без визуала = брак.
— ДИСЦИПЛИНА ЭФФЕКТОВ: ОДНА сигнатура на секцию, ДОЗОЙ, ТОН-В-ТОН (одна хью-семья). НЕ лепи aurora+beams+meteors разом — дешёвый AI-цирк. Эффект под вайб: .fx-waves=органик/фуд, .fx-beams/.fx-meteors=тёмный премиум/техно, .fx-grid-glow=редактура/SaaS, .fx-aurora-soft=мягкий бренд.
— ИНТРО→СТРАНИЦА (усиливает первое впечатление, если уместно вайбу): короткий прелоадер-оверлей через класс `.omnia-intro` (fixed, бренд-вордмарк/лого по центру, без рисованных фигур) — КИТ САМ плавно растворяет его в hero (CSS-авто-fade ~1.6s, reduced-motion прячет). Один на страницу, первым в body. НЕ пиши свой fade-JS — только класс `.omnia-intro`.
— ОБЯЗАТЕЛЬНЫЙ СИГНАТУРНЫЙ СКРОЛЛ-МОМЕНТ (anti-«статичный шаблон» — это ЖЕЛЕЗНО): страница ОБЯЗАНА нести РОВНО ОДИН крупный «дорогой» скролл-момент. Выбери под бизнес и впиши в нужную секцию: `.pin-stage` (процесс / этапы / трансформация / продукт по шагам), `.compare` (до/после видимого результата услуги: ремонт/бьюти/стома/детейлинг/фитнес), `.omnia-draw` (рисующийся line-art путь в «как это работает» или связке) ИЛИ `.scroll-clip-reveal` на герое/крупном кадре. ОДИН, не три — и НЕ выкидывай его. Точный markup pin-stage/compare/line-draw — в блоках KIT v5/v6 системного промпта выше; перенеси его в спеку секции дословно.
— .omnia-shader: ВСЕГДА задавай data-omnia-colors="#..,#..,#..,#.." (4 тон-в-тон HEX одной хью-семьи) — иначе фон уедет в дефолт. Это и есть цвет градиента.
— Акцент дозой (CTA/цифры/иконки), НЕ заливкой блоков. Цвет — иерархией.
— Один primary-CTA на экран. Кнопки объёмные (.btn-cta-primary). Иконки — SVG, НЕ эмодзи.
— Реальные контакты/бейджи/цифры. Ноль «ваш текст», «слоган здесь», lorem.

Пиши плотно: каждая строка — принятое решение с конкретикой и готовым текстом.
Только текст брифа, без кода."""


# Pass 2 — the writer realises the brief. ``{brief}`` is injected verbatim.
_WRITER_INSTRUCTION_TEMPLATE = """\
Ты — ВЕРСТАЛЬЩИК-ИСПОЛНИТЕЛЬ (проход 2 из 2). Арт-директор уже принял ВСЕ решения —
бриф ниже это готовая СПЕЦИФИКАЦИЯ. Ты НЕ дизайнер: ты ТРАНСКРИБИРУЕШЬ бриф в HTML.

ЖЕЛЕЗНЫЕ ПРАВИЛА:
• Каждый HEX, шрифт, класс, отступ, заголовок, подзаголовок, булит, лейбл кнопки,
  цену, имя, телефон и img-prompt бери ИЗ БРИФА ДОСЛОВНО — слово в слово. Ничего не
  выдумывай, не сокращай, не упрощай, не переименовывай, не выкидывай.
• Секции — в том же порядке, с теми же id, тем же текстом, теми же kit-классами и
  эффектами, что в брифе. Если поле есть в брифе — оно ОБЯЗАНО быть в HTML.
• Картинки — ровно те <img data-omnia-gen="..."> с prompt'ами из брифа, на своих
  местах, с указанными классами.
• Инлайн-<svg> из брифа — вставляй КОД ДОСЛОВНО (1:1), не перерисовывай, не
  упрощай, не выкидывай. Это готовый рисунок арт-директора, не твой.
• Соблюдай системный промпт выше: контракт «ноль тупиков» (живые ссылки/кнопки),
  kit-классы, и выдай ответ РОВНО в том формате (<file ...>), который требует
  системный промпт.
• Твоя единственная свобода — чистая, валидная, адаптивная реализация. Дизайн уже
  сделан до тебя.

ДИСЦИПЛИНА (ровно то, по чему страницу проверяют автоматом — нарушил = брак):
• ШРИФТЫ: только 2 семейства из брифа, ≤6 размеров, ≤4 начертания на ВСЮ страницу.
  Не плоди text-7xl/text-8xl сверх брифа — лишний размер = брак.
• КНОПКИ: РОВНО ОДНА .btn-cta-primary на всю страницу (та, что указана в брифе).
  Остальные кнопки — вторичные (outline / сплошная нейтраль), НЕ .btn-cta-primary.
  Любая кликабельная кнопка: min-height 44px и АСИММЕТРИЧНЫЙ паддинг (px ≥ 2×py,
  напр. px-8 py-3). НЕ делай px=py (px-6 py-6 = брак).
• ДОСТУПНОСТЬ: у каждого <img> — осмысленный alt; у каждого input/select/textarea —
  <label> или aria-label или placeholder. РОВНО один <h1> на страницу, остальные
  заголовки <h2>/<h3>.
• ЦВЕТ: только HEX из брифа. Акцент — дозой (CTA/цифры/иконки), НЕ заливкой блоков.

САМОПРОВЕРКА ПЕРЕД ВЫВОДОМ (обязательна — тихо сверься, исправь, потом выводи):
1. Все секции брифа на месте, в том же порядке, с теми же id?
2. Каждый HEX / шрифт / класс / лейбл / цена / телефон — ДОСЛОВНО из брифа?
3. Каждый <img data-omnia-gen="..."> из брифа на месте — со своим prompt'ом и классами?
4. РОВНО одна .btn-cta-primary? ≤2 семейства / ≤6 размеров / ≤4 начертания шрифта?
5. Кнопки ≥44px с асимметричным паддингом? У всех <img> alt, у всех полей label?
6. Один <h1>? Живые href (ноль тупиков)? Формат <file ...> соблюдён?
7. Шапка — НЕ дефолт «лого-слева + нав-центр + кнопка-справа», а тот архетип из брифа? ГЕРОЙ реализован заявленным в брифе режимом (ГЕРОЙ-РЕЖИМ I тип-герой / II фото-арт-герой), а НЕ вялый мелкий заголовок по центру на плоском фоне?
8. В КАЖДОЙ секции есть визуал/граф-слой (рисованный SVG / эффект-класс кита / типографика-графика)? На странице есть ≥1 КРУПНЫЙ рисованный тематический SVG-момент? Рисованный SVG чистый (viewBox, аккуратные stroke), не клипарт?
9. Если в брифе есть интро/прелоадер — реализован через `.omnia-intro` (НЕ выкинут, свой fade-JS НЕ писал)? У каждого `.omnia-shader` стоит data-omnia-colors с 4 тон-в-тон HEX из брифа?
10. Сигнатурный скролл-момент из брифа реализован РОВНО один раз и НЕ выкинут? Его markup на месте и целый (`.pin-stage` → data-pin-stage + парные data-pin-layer/data-pin-step; `.compare` → .compare-after/.compare-before/.compare-range; `.omnia-draw` → <svg> с <path>; либо `.scroll-clip-reveal`)?
Нашёл отклонение — исправь ДО вывода. Выводи только финальный HTML, без объяснений.

ДИЗАЙН-БРИФ (исполнять буквально, слово в слово):
<<<БРИФ
{brief}
БРИФ>>>"""


def _aggregate_usage(*usages: dict[str, Any] | None) -> dict[str, Any]:
    """Sum tokens / cost across pass usages. None entries treated as zeros."""
    return {
        "tokens_in": sum(int((u or {}).get("tokens_in", 0)) for u in usages),
        "tokens_out": sum(int((u or {}).get("tokens_out", 0)) for u in usages),
        "cost_rub": sum(float((u or {}).get("cost_rub", 0.0)) for u in usages),
        "passes": len([u for u in usages if u is not None]),
    }


def _build_art_director_messages(
    base_messages: list[dict[str, str]],
    user_prompt: str,
    model_id: str | None,
) -> list[dict[str, str]]:
    """Art-Director pass: shared system prompt, last user turn appends the
    brief directive. ``json_strict=False`` — the brief is prose, not JSON."""
    directive = vendor_directive(model_id, json_strict=False)
    suffix = f"\n\n{directive}" if directive else ""
    msgs = list(base_messages[:-1])
    msgs.append({
        "role": "user",
        "content": f"{user_prompt}\n\n{_ART_DIRECTOR_INSTRUCTION}{suffix}",
    })
    return msgs


def _build_writer_messages(
    base_messages: list[dict[str, str]],
    user_prompt: str,
    brief: str,
    model_id: str | None,
) -> list[dict[str, str]]:
    """Writer pass: shared system prompt + the brief injected into the last
    user turn. ``json_strict=False`` — freeform HTML, never a JSON nudge. An
    empty ``brief`` degrades to the base freeform prompt (R-10 fail-soft)."""
    directive = vendor_directive(model_id, json_strict=False)
    suffix = f"\n\n{directive}" if directive else ""
    msgs = list(base_messages[:-1])
    if brief:
        tail = _WRITER_INSTRUCTION_TEMPLATE.format(brief=brief)
        content = f"{user_prompt}\n\n{tail}{suffix}"
    else:
        content = f"{user_prompt}{suffix}"
    msgs.append({"role": "user", "content": content})
    return msgs


async def art_director_writer_generate(
    *,
    base_messages: list[dict[str, str]],
    user_prompt: str,
    art_director_model: str | None = None,
    writer_model: str | None = None,
    user_id: UUID,
    project_id: UUID,
    message_id: UUID,
) -> AsyncIterator[dict[str, Any]]:
    """Run Art-Director (Opus, brief) → Writer (DeepSeek, HTML).

    Pass 1 streams silently and accumulates the design brief. Pass 2 streams its
    chunks to the caller as the FINAL HTML, executing the brief. Per-pass models
    default from ``model_for_role`` but can be forced (admin override). The
    yielded events match ``stream_chat_completion`` so the caller is agnostic to
    the 2-pass split.
    """
    art_director_model = art_director_model or model_for_role("art_director")
    writer_model = writer_model or model_for_role("freeform_writer")

    # ─── Pass 1: Art-Director (silent — accumulate the brief) ────────────
    yield {"pass": "art_director", "stage": "start", "model": art_director_model}
    ad_msgs = _build_art_director_messages(base_messages, user_prompt, art_director_model)
    if pipeline_debug.enabled():
        _sys = next((m["content"] for m in base_messages if m.get("role") == "system"), "")
        pipeline_debug.dump(project_id, message_id, "00_system_prompt.md", _sys)
        pipeline_debug.dump(project_id, message_id, "01_art_director_input.md", ad_msgs[-1]["content"])
    brief_parts: list[str] = []
    ad_usage: dict[str, Any] | None = None
    async for event in stream_chat_completion(
        ad_msgs,
        art_director_model,
        str(user_id),
        str(project_id),
        str(message_id),
    ):
        if delta := event.get("delta"):
            brief_parts.append(delta)
        if u := event.get("usage"):
            ad_usage = u
        if err := event.get("error"):
            # The brief failed — let the Writer carry the page alone rather than
            # losing the whole build (R-10 fail-soft). Don't propagate the error.
            brief_parts = []
            break

    brief = "".join(brief_parts).strip()
    pipeline_debug.dump(project_id, message_id, "02_brief.md", brief)
    yield {"pass": "art_director", "stage": "end", "chars": len(brief)}

    # ─── Pass 2: Writer (streams the HTML to the caller) ─────────────────
    yield {"pass": "writer", "stage": "start", "model": writer_model}
    writer_msgs = _build_writer_messages(base_messages, user_prompt, brief, writer_model)
    if pipeline_debug.enabled():
        pipeline_debug.dump(project_id, message_id, "01b_writer_input.md", writer_msgs[-1]["content"])
    writer_usage: dict[str, Any] | None = None
    writer_parts: list[str] = []
    async for event in stream_chat_completion(
        writer_msgs,
        writer_model,
        str(user_id),
        str(project_id),
        str(message_id),
    ):
        if delta := event.get("delta"):
            writer_parts.append(delta)
            yield {"delta": delta}
        if u := event.get("usage"):
            writer_usage = u
        if err := event.get("error"):
            yield {"error": f"writer pass failed: {err}"}
            return

    pipeline_debug.dump(project_id, message_id, "03_writer_raw.html", "".join(writer_parts))
    yield {"pass": "writer", "stage": "end"}
    yield {"usage": _aggregate_usage(ad_usage, writer_usage)}


__all__ = ["art_director_writer_generate"]
