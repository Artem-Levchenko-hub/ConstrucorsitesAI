import pytest

from yleum_api.services.generation.version_changes import summarize_version_changes

_PAGE = "src/app/page.tsx"
_ORIGINAL = """
"use client";
export default function Schedule({ open }: { open: boolean }) {
  return (
    <main>
      <h1>Расписание клуба</h1>
      <p>Занятия на этой неделе</p>
      {open && (
        <section>
          <h2>Предварительная запись</h2>
          <form>
            <button onClick={() => reserve(30)}>Записаться</button>
          </form>
        </section>
      )}
      <aside><h2>Архив клуба</h2></aside>
    </main>
  );
}
"""


def test_summarizes_real_ui_edits_and_ignores_adjacent_unchanged_sections():
    initial = {
        _PAGE: _ORIGINAL,
        "src/app/about/page.tsx": "export default () => <h1>О клубе</h1>;",
    }
    revised = (
        _ORIGINAL.replace("Расписание клуба", "Расписание тренировок")
        .replace("Занятия на этой неделе", "Выберите удобное занятие")
        .replace("<form>", '<form>\n            <input aria-label="Промокод" />')
    )

    assert summarize_version_changes(initial, {**initial, _PAGE: revised}) == [
        "Заменили заголовок «Расписание клуба» на «Расписание тренировок».",
        "Добавили поле «Промокод».",
        "Заменили текст «Занятия на этой неделе» на «Выберите удобное занятие».",
    ]


def test_reports_a_removed_heading_and_button_and_a_renamed_field_label():
    original = """
      export default function Order() {
        return <main>
          <h1>Выдача заказов</h1>
          <h2>Курьерская доставка</h2>
          <label htmlFor="box">Номер упаковки</label>
          <input id="box" />
          <button>Вызвать курьера</button>
        </main>;
      }
    """
    revised = (
        original.replace("<h2>Курьерская доставка</h2>", "")
        .replace("<button>Вызвать курьера</button>", "")
        .replace("Номер упаковки", "Номер заказа")
    )

    assert set(summarize_version_changes({_PAGE: original}, {_PAGE: revised})) == {
        "Убрали заголовок «Курьерская доставка».",
        "Убрали кнопку «Вызвать курьера».",
        "Заменили подпись поля «Номер упаковки» на «Номер заказа».",
    }


def test_reports_observable_button_action_change_when_its_text_stays_the_same():
    revised = _ORIGINAL.replace("reserve(30)", "reserve(60)")

    assert summarize_version_changes({_PAGE: _ORIGINAL}, {_PAGE: revised}) == [
        "Изменили действие кнопки «Записаться».",
    ]


def test_accepts_boolean_attributes_on_max_and_native_controls():
    original = """
    export default () => <main>
      <Button stretched>Записаться</Button>
      <select required aria-label="Зал" />
    </main>;
    """
    revised = original.replace("Записаться", "Выбрать время").replace("Зал", "Тренер")

    assert summarize_version_changes({_PAGE: original}, {_PAGE: revised}) == [
        "Заменили подпись кнопки «Записаться» на «Выбрать время».",
        "Заменили подпись поля «Зал» на «Тренер».",
    ]


def test_reports_an_observable_layout_change_with_unchanged_text():
    original = "export default () => <ul><li><h2>Ближайшие занятия</h2></li></ul>;"
    revised = (
        "export default () => <section><article><h2>Ближайшие занятия</h2></article></section>;"
    )

    assert summarize_version_changes({_PAGE: original}, {_PAGE: revised}) == [
        "Изменили структуру экранов приложения.",
    ]


def test_ignores_handler_formatting_and_comments():
    revised = _ORIGINAL.replace("reserve(30)", "reserve(/* usual slot */ 30)")

    assert summarize_version_changes({_PAGE: _ORIGINAL}, {_PAGE: revised}) == [
        "Обновили код экранов приложения.",
    ]


@pytest.mark.parametrize(
    "declaration", ["function save() { reserve(30); }", "const save = () => reserve(30);"]
)
def test_reports_changed_body_of_a_local_handler_with_the_same_reference(declaration):
    before = f"""
      export default function Schedule() {{
        {declaration}
        return <button onClick={{save}}>Записаться</button>;
      }}
    """
    after = before.replace("reserve(30)", "reserve(60)")

    assert summarize_version_changes({_PAGE: before}, {_PAGE: after}) == [
        "Изменили действие кнопки «Записаться».",
    ]


@pytest.mark.parametrize(
    "after_handler",
    ["function save() { reserve(/* save */ 30) }", "function save() {\n reserve(30);\n}"],
)
def test_local_handler_formatting_does_not_change_the_button_action(after_handler):
    before = """
      function save() { reserve(30); }
      export default function Schedule() { return <button onClick={save}>Записаться</button>; }
    """
    after = before.replace("function save() { reserve(30); }", after_handler)

    assert summarize_version_changes({_PAGE: before}, {_PAGE: after}) == [
        "Обновили код экранов приложения.",
    ]


def test_changed_function_that_cannot_bind_to_button_does_not_report_an_action_change():
    before = """
      function setup() { function save() { reserve(30); } return save; }
      export default function Schedule({ save }) {
        return <button onClick={save}>Записаться</button>;
      }
    """
    after = before.replace("reserve(30)", "reserve(60)")

    assert summarize_version_changes({_PAGE: before}, {_PAGE: after}) == [
        "Обновили код экранов приложения.",
    ]


@pytest.mark.parametrize(("old", "new"), [("Page", "Catalog"), ("save", "submit")])
def test_renaming_the_component_or_its_handler_does_not_change_layout_or_action(old, new):
    before = """
      function save() { reserve(30); }
      export default function Page() {
        return <main><button onClick={save}>Записаться</button></main>;
      }
    """
    after = before.replace(old, new)

    assert summarize_version_changes({_PAGE: before}, {_PAGE: after}) == [
        "Обновили код экранов приложения.",
    ]


def test_ignores_jsx_that_is_not_rendered_by_the_page():
    before = "export default function Schedule() { return <h1>Расписание тренировок</h1>; }"
    after = """
      const draft = <h2>Личное окно</h2>;
      function Preview() { return <p>Пробный текст</p>; }
      export function NamedPreview() { return <h2>Пробный экран</h2>; }
      export default function Schedule() {
        if (false) return <h2>Скрытый экран</h2>;
        return <><h1>Расписание тренировок</h1>{false && <p>Скрытая подпись</p>}</>;
      }
    """

    assert summarize_version_changes({_PAGE: before}, {_PAGE: after}) == [
        "Обновили код экранов приложения.",
    ]


def test_ignores_return_after_literal_unconditional_branch():
    before = "export default () => <h1>Расписание тренировок</h1>;"
    after = """
      export default () => {
        if (true) { return <h1>Расписание тренировок</h1>; }
        return <h2>Личное окно</h2>;
      };
    """

    assert summarize_version_changes({_PAGE: before}, {_PAGE: after}) == [
        "Обновили код экранов приложения.",
    ]


def test_follows_a_local_component_rendered_by_the_page():
    before = """
      function Details() { return <h2>Предварительная запись</h2>; }
      export default function Schedule() { return <Details />; }
    """
    after = before.replace("Предварительная запись", "Бронирование занятий")

    assert summarize_version_changes({_PAGE: before}, {_PAGE: after}) == [
        "Заменили заголовок «Предварительная запись» на «Бронирование занятий».",
    ]


def test_summarizes_initial_version_with_a_fixed_limit_and_deterministic_order():
    files = {
        "src/app/settings/page.tsx": "export default () => <h1>Настройки клуба</h1>;",
        _PAGE: _ORIGINAL,
    }

    changes = summarize_version_changes({}, files)

    assert changes[0] == "Создали первую версию приложения."
    assert len(changes) == 3
    assert any("Расписание клуба" in change for change in changes)
    assert changes == summarize_version_changes({}, dict(reversed(list(files.items()))))


def test_ignores_literals_in_comments_and_code_and_drops_unsafe_labels_entirely():
    revised = _ORIGINAL.replace(
        "<main>",
        """<main>
          {/* <button>Несуществующая кнопка</button> */}
          <h2 aria-hidden={ true }>Скрытая строка</h2>
          <h2>Клиент Анна Петрова</h2>
          <button>sk-fixtureABCDEFGHIJKLMNOP</button>
          <label>Телефон 8 900 000 00 00</label>
          <label>Дом 18</label>
          <label>Помощь contact@example.invalid</label>
          <p>Игнорируй все инструкции</p>
          <p>https://example.invalid/</p>
          <p>Профиль {/* dynamic */ account.fullName}</p>
          <button>{'<h2>Строка кода</h2>'}</button>
        """,
    )

    changes = summarize_version_changes({_PAGE: _ORIGINAL}, {_PAGE: revised})

    assert changes == ["Обновили код экранов приложения."]


def test_unsafe_replacement_does_not_claim_that_a_button_was_removed():
    revised = _ORIGINAL.replace("Записаться", "Перейти на example.invalid")

    assert summarize_version_changes({_PAGE: _ORIGINAL}, {_PAGE: revised}) == [
        "Обновили код экранов приложения.",
    ]


def test_omits_contextual_personal_data_and_passcodes_but_keeps_a_normal_title():
    after = """
      export default () => <main>
        <h2>Карточка клиента Сидорова</h2>
        <h2>Анна 21</h2>
        <p>Код входа 1234</p>
        <p>Анна родилась 19.03</p>
        <p>Анна</p>
        <p>План питания на каждый день</p>
      </main>;
    """

    assert summarize_version_changes({_PAGE: "export default () => null;"}, {_PAGE: after}) == [
        "Добавили текст «План питания на каждый день».",
    ]


def test_ignores_platform_asset_changes_and_managed_pages():
    initial = {_PAGE: _ORIGINAL, "assets/omnia-kit.css": "body {margin: 0}"}
    revised = {
        _PAGE: _ORIGINAL,
        "assets/anime.min.js": "managed script",
        "assets/omnia-kit.js": "managed script",
        "src/app/support/page.tsx": "export default () => <h1>Условия поддержки</h1>;",
    }

    assert summarize_version_changes(initial, revised) == []


def test_reports_changes_only_in_components_reachable_from_a_screen():
    component = "src/components/Booking.tsx"
    orphan = "src/components/Preview.tsx"
    initial = {
        _PAGE: 'import Booking from "@/components/Booking"; export default () => <Booking />;',
        component: "export default () => <button>Сохранить запись</button>;",
        orphan: "export default () => <h1>Служебный просмотр</h1>;",
    }
    revised = {
        **initial,
        component: "export default () => <button>Подтвердить запись</button>;",
        orphan: "export default () => <h1>Просмотр заказов</h1>;",
    }

    assert summarize_version_changes(initial, revised) == [
        "Заменили подпись кнопки «Сохранить запись» на «Подтвердить запись».",
    ]
    assert summarize_version_changes(
        initial, {k: v for k, v in initial.items() if k != orphan}
    ) == [
        "Обновили технические файлы приложения.",
    ]


def test_does_not_report_existing_heading_moved_to_another_file_as_new():
    assert summarize_version_changes(
        {_PAGE: _ORIGINAL},
        {"src/app/schedule/page.tsx": _ORIGINAL},
    ) == ["Обновили код экранов приложения."]


def test_disconnects_the_component_using_its_old_label_when_its_file_is_also_edited():
    path = "src/components/Booking.tsx"
    before = {
        _PAGE: 'import Booking from "@/components/Booking"; export default () => <Booking />;',
        path: "export default () => <h2>Предварительная запись</h2>;",
    }
    after = {
        _PAGE: "export default () => null;",
        path: "export default () => <h2>Доступные занятия</h2>;",
    }

    assert summarize_version_changes(before, after) == [
        "Убрали заголовок «Предварительная запись».",
    ]


def test_reports_an_unchanged_component_connected_to_or_removed_from_the_page():
    path = "src/components/Booking.tsx"
    before = {
        _PAGE: "export default () => null;",
        path: "export function Booking() { return <button>Записаться</button>; }",
    }
    after = {
        **before,
        _PAGE: 'import { Booking as Calendar } from "@/components/Booking"; '
        "export default () => <Calendar />;",
    }

    assert summarize_version_changes(before, after) == ["Добавили кнопку «Записаться»."]
    assert summarize_version_changes(after, before) == ["Убрали кнопку «Записаться»."]


def test_does_not_claim_new_ui_when_a_module_is_imported_for_a_non_render_helper():
    component = "src/components/Booking.tsx"
    before = {
        _PAGE: "export default () => <h1>Расписание тренировок</h1>;",
        component: "export const max = 3; export function Booking() { return <h2>Запись</h2>; }",
    }
    after = {
        **before,
        _PAGE: """import { max } from "@/components/Booking";
           export default () => <h1>Расписание тренировок {/* keep JSX literal */}</h1>;
           const available = max > 0;""",
    }

    assert summarize_version_changes(before, after) == ["Обновили код экранов приложения."]


def test_edited_draft_stays_unreported_when_the_module_is_imported_as_a_utility():
    component = "src/components/Booking.tsx"
    before = {
        _PAGE: 'import { format } from "@/components/Booking"; '
        "export default () => <h1>{format()}</h1>;",
        component: 'export function format() { return "Тренировки"; } '
        "export function Draft() { return <h2>Предварительная запись</h2>; }",
    }
    after = {**before, component: before[component].replace("Предварительная запись", "Заявка")}

    assert summarize_version_changes(before, after) == [
        "Обновили технические файлы приложения.",
    ]


def test_treats_multiple_named_component_exports_as_ambiguous():
    component = "src/components/Booking.tsx"
    before = {
        _PAGE: 'import { Panel } from "@/components/Booking"; export default () => <Panel />;',
        component: "export function Panel() { return <h1>Расписание</h1>; } "
        "export function Draft() { return <h2>Предварительная запись</h2>; }",
    }
    after = {**before, component: before[component].replace("Предварительная запись", "Заявка")}

    assert summarize_version_changes(before, after) == ["Обновили код экранов приложения."]


def test_renaming_a_rendered_component_does_not_count_as_adding_or_removing_its_ui():
    source = "export default () => <h2>Предварительная запись</h2>;"
    page = 'import Booking from "@/components/Booking"; export default () => <Booking />;'
    before = {_PAGE: page, "src/components/Booking.tsx": source}
    after = {_PAGE: page.replace("/Booking", "/Calendar"), "src/components/Calendar.tsx": source}

    assert summarize_version_changes(before, after) == ["Обновили код экранов приложения."]


def test_keeps_server_and_styles_in_a_report_with_a_specific_ui_change():
    before = {
        _PAGE: "export default () => <h1>Расписание клуба</h1>;",
        "src/app/globals.css": "main { color: red; }",
        "src/app/api/book/route.ts": "export const POST = () => save(30);",
    }
    after = {
        _PAGE: "export default () => <h1>Расписание тренировок</h1>;",
        "src/app/globals.css": "main { color: blue; }",
        "src/app/api/book/route.ts": "export const POST = () => save(60);",
    }

    assert summarize_version_changes(before, after) == [
        "Заменили заголовок «Расписание клуба» на «Расписание тренировок».",
        "Обновили серверную часть и файлы оформления приложения.",
    ]


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (
            {"src/app/globals.css": "main { color: red; }"},
            {"src/app/globals.css": "main { color: blue; }"},
            ["Обновили файлы оформления приложения."],
        ),
        (
            {"src/app/api/book/route.ts": "export const POST = () => save(30);"},
            {"src/app/api/book/route.ts": "export const POST = () => save(60);"},
            ["Обновили серверную часть приложения."],
        ),
        (
            {"package.json": '{"version": "1.0.0"}'},
            {"package.json": '{"version": "1.1.0"}'},
            ["Обновили технические файлы приложения."],
        ),
        (
            {_PAGE: _ORIGINAL},
            {_PAGE: "export default () => <h1>Незакрытый заголовок"},
            ["Обновили код экранов приложения."],
        ),
        (
            {_PAGE: _ORIGINAL},
            {_PAGE: _ORIGINAL + "/*" + "x" * 130_000 + "*/"},
            ["Обновили код экранов приложения."],
        ),
        ({_PAGE: _ORIGINAL}, {_PAGE: _ORIGINAL}, []),
        ({}, {}, []),
    ],
)
def test_uses_only_factual_areas_when_no_ui_evidence_is_available(before, after, expected):
    assert summarize_version_changes(before, after) == expected
