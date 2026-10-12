"""Reduced regression for accepted Fitness run 222be365, not its full source.

The real QA receipt established the unreferenced setter and zero-match script.
These tests retain that failure without private application data or provider calls.
"""
from __future__ import annotations

import json

import pytest

from yleum_api.services.max_generation_contract import max_source_completion_gap

PROMPT = "Добавь видимые кнопки фильтра: Все / До 30 минут / Больше 30 минут."
PAGE = '''"use client";
import { useState } from "react";
export default function Page({workouts}: {workouts: {id:string;duration:number}[]}) {
  const [filter, setFilter] = useState<"all" | "short" | "long">("all");
  const shown = workouts.filter(w => filter === "all" ||
    (filter === "short" ? w.duration <= 30 : w.duration > 30));
  return <main>{shown.map(w => <article key={w.id}>{w.duration}</article>)}</main>;
}'''


def candidate(page: str = PAGE) -> dict[str, str]:
    return {
        ".omnia/cell.json": json.dumps({
            "version": 1,
            "tasks": [{"name": "final-test", "role": "full_build", "argv": ["pnpm", "test"]}],
            "services": [{"name": "web", "argv": ["pnpm", "start"]}],
            "routes": [{"path": "/", "service": "web", "port": 3000}],
        }),
        "src/app/page.tsx": page,
    }


def test_zero_match_script_cannot_supply_controls_to_accepted_root() -> None:
    files = candidate()
    search = "{workouts.length === 0 && !loading && !error ? ("
    replacement = '<button onClick={() => setFilter("all")}>Все</button>'
    assert search not in files["src/app/page.tsx"]
    # Equivalent of String.replace in the reported helper: no mutation, no error.
    files["src/app/page.tsx"] = files["src/app/page.tsx"].replace(search, replacement, 1)
    files["patch.js"] = (
        "const inserted = 'Все / До 30 минут / Больше 30 минут; setFilter(all)';\n"
        "console.log('Patch applied successfully');"
    )
    gap = max_source_completion_gap(PROMPT, files, portable=True)
    assert gap is not None and "setFilter" in gap and "src/app/page.tsx" in gap


def test_unused_filter_setter_is_not_a_source_completion() -> None:
    assert "setFilter" in str(max_source_completion_gap(PROMPT, candidate(), portable=True))


@pytest.mark.parametrize("handler", [
    '<button onClick={() => setFilter("short")}>До 30 минут</button>',
    '<FilterControls onChange={setFilter}/>',
    'const change = setFilter; '
    'return <button onClick={() => change("short")}>До 30 минут</button>;',
    'const info = `value ${setFilter("short")}`;',
    '// setFilter is referenced here; conservative source analysis stays unknown',
    'const explanation = "setFilter";',
])
def test_any_other_setter_reference_is_unknown_not_a_false_rejection(handler: str) -> None:
    page = PAGE.replace("return <main>", handler + "\nreturn <main>")
    assert max_source_completion_gap(PROMPT, candidate(page), portable=True) is None


@pytest.mark.parametrize("prompt", [
    "Добавь описание тренировок, фильтры не нужны.",
    "Не добавляй кнопки фильтра.",
    "API-only: add filters for workouts.",
    "Только API: добавь фильтры тренировок.",
    "Объясни, почему кнопки фильтра отсутствуют.",
    "Review the existing visible filter buttons.",
    "Добавь фильтры в SQL, интерфейс не менять.",
])
def test_noninteractive_or_excluded_requests_keep_existing_acceptance(prompt: str) -> None:
    assert max_source_completion_gap(prompt, candidate(), portable=True) is None


@pytest.mark.parametrize("change", [
    ('import { useState } from "react";', 'import { useState } from "./custom";'),
    ('filter ===', 'other ==='),
    ('return <main>', 'const selected = useSearchParams(); return <main>'),
    ('return <main>', '<DurationFilterControls/>; return <main>'),
    ('return <main>', 'const [otherFilter,setOtherFilter]=useState("all"); '
     'const alternate=workouts.filter(w=>otherFilter === "all"); '
     'setOtherFilter("short"); return <main>'),
    ('const [filter, setFilter]', 'const [\u0066ilter, set\u0046ilter]'),
])
def test_unsupported_or_alternate_filter_implementations_are_not_rejected(
    change: tuple[str, str],
) -> None:
    # The escape case deliberately produces equivalent source; test actual JS escapes.
    if change[0] == 'const [filter, setFilter]':
        page = PAGE.replace(change[0], r'const [\u0066ilter, set\u0046ilter]')
    else:
        page = PAGE.replace(*change)
    assert max_source_completion_gap(PROMPT, candidate(page), portable=True) is None


@pytest.mark.parametrize("alias,import_line", [
    ("state", 'import { useState as state } from "react";'),
    ("React.useState", 'import React from "react";'),
])
def test_known_react_import_aliases_still_report_unreferenced_filter(
    alias: str, import_line: str,
) -> None:
    page = PAGE.replace('import { useState } from "react";', import_line)
    page = page.replace("= useState<", "= " + alias + "<")
    assert "setFilter" in str(max_source_completion_gap(PROMPT, candidate(page), portable=True))


@pytest.mark.parametrize("page", [
    PAGE.replace("export default function Page", "function OldList")
    + '\nexport default function Page(){ const [selected,setSelected]=useState("all"); '
    'return <button onClick={()=>setSelected("short")}>До 30 минут</button>;}',
    PAGE.replace(
        'filter === "all" ||\n    (filter === "short" ? w.duration <= 30 : w.duration > 30)',
        'w.duration > 0 && ({filter: true}).filter',
    ),
    PAGE.replace('workouts.filter(w => filter', 'workouts.filter(filter => filter'),
    PAGE.replace('function Page({workouts}', 'function Page({workouts,useState}'),
    '''import {useState} from "react";
export default function Page(){
  const example = /const [filter, setFilter] = useState("all"); rows.filter(item => filter)/;
  return <main>{String(example)}</main>;
}''',
])
def test_review_falsifiers_skip_unsupported_scopes_and_regex(page: str) -> None:
    assert max_source_completion_gap(PROMPT, candidate(page), portable=True) is None


SORT_PROMPT = "Добавь видимые кнопки сортировки каталога по цене: Исходный / Дешевле / Дороже."
SORT_PAGE = '''"use client";
import {useState} from "react";
export default function Page({filteredCatalog}: {filteredCatalog: {id:string;price:number}[]}) {
  const [sortOrder,setSortOrder]=useState<"original"|"asc"|"desc">("original");
  const sortedCatalog=[...filteredCatalog].sort((a,b)=>sortOrder === "asc"
    ? a.price-b.price : sortOrder === "desc" ? b.price-a.price : 0);
  return <main>{filteredCatalog.map(item=><article key={item.id}>{item.price}</article>)}</main>;
}'''


def test_coffee_sort_setter_and_result_missing_is_not_source_completion() -> None:
    files = candidate(SORT_PAGE)
    assert "setSortOrder" in str(max_source_completion_gap(SORT_PROMPT, files, portable=True))


def test_control_connected_but_sorted_result_not_rendered_is_still_incomplete() -> None:
    page = SORT_PAGE.replace(
        "return <main>",
        'return <main><button onClick={()=>setSortOrder("asc")}>Дешевле</button>',
    )
    gap = max_source_completion_gap(SORT_PROMPT, candidate(page), portable=True)
    assert gap is not None and "sortedCatalog" in gap


def test_reachable_sort_controls_and_collection_have_no_negative_witness() -> None:
    page = SORT_PAGE.replace(
        "return <main>",
        'return <main><button onClick={()=>setSortOrder("asc")}>Дешевле</button>',
    ).replace("{filteredCatalog.map", "{sortedCatalog.map")
    assert max_source_completion_gap(SORT_PROMPT, candidate(page), portable=True) is None


@pytest.mark.parametrize("initial", ["0", "() => 0"])
def test_numeric_and_lazy_alternative_filter_state_do_not_falsely_reject(initial: str) -> None:
    page = PAGE.replace(
        "return <main>",
        'const [duration,setDuration]=useState(' + initial + ');\n'
        'const visible=workouts.filter(w=>duration === 0 || w.duration<=30);\n'
        'return <main><button onClick={()=>setDuration(1)}>До 30 минут</button>',
    ).replace("{shown.map", "{visible.map")
    assert max_source_completion_gap(PROMPT, candidate(page), portable=True) is None


def test_in_place_sort_with_rendered_receiver_is_not_rejected() -> None:
    page = SORT_PAGE.replace('[...filteredCatalog].sort', 'filteredCatalog.sort').replace(
        'return <main>',
        'return <main><button onClick={()=>setSortOrder("asc")}>Дешевле</button>',
    )
    assert max_source_completion_gap(SORT_PROMPT, candidate(page), portable=True) is None


def test_unexecuted_collection_initializer_is_not_root_dataflow() -> None:
    page = PAGE.replace('const shown = workouts.filter', 'const Legacy = () => workouts.filter')
    page = page.replace('{shown.map(w => <article key={w.id}>{w.duration}</article>)}',
                        '<ActualControls workouts={workouts}></ActualControls>')
    assert max_source_completion_gap(PROMPT, candidate(page), portable=True) is None


def test_nested_callback_function_shadow_is_not_root_state() -> None:
    page = PAGE.replace(
        'filter === "all" ||\n    (filter === "short" ? w.duration <= 30 : w.duration > 30)',
        '(() => { function filter() {} return !!filter; })()',
    )
    assert max_source_completion_gap(PROMPT, candidate(page), portable=True) is None


def test_named_predicate_shadow_is_not_root_state() -> None:
    page = PAGE.replace(
        'w => filter === "all" ||\n    (filter === "short" ? w.duration <= 30 : w.duration > 30)',
        'function filter(w) { return !!filter && w.duration > 0; }',
    )
    assert max_source_completion_gap(PROMPT, candidate(page), portable=True) is None


def test_local_function_shadows_imported_hook() -> None:
    page = PAGE.replace('  const [filter,',
        '  function useState<T>(value:T):[T,(value:T)=>void] { return [value,()=>{}]; }\n'
        '  const [filter,')
    assert max_source_completion_gap(PROMPT, candidate(page), portable=True) is None


def test_active_alternative_filter_in_jsx_is_unknown() -> None:
    page = PAGE.replace('return <main>',
        'const [duration,setDuration]=useState(0);\n'
        'return <main><button onClick={()=>setDuration(30)}>До 30 минут</button>')
    page = page.replace('{shown.map',
        '{workouts.filter(w=>duration === 0 || w.duration <= duration).map')
    assert max_source_completion_gap(PROMPT, candidate(page), portable=True) is None


def test_same_state_inline_sort_is_not_a_dead_result_witness() -> None:
    page = SORT_PAGE.replace('return <main>',
        'return <main><button onClick={()=>setSortOrder("asc")}>Дешевле</button>')
    page = page.replace('{filteredCatalog.map',
        '{[...filteredCatalog].sort((a,b)=>sortOrder === "asc" ? '
        'a.price-b.price : b.price-a.price).map')
    assert max_source_completion_gap(SORT_PROMPT, candidate(page), portable=True) is None


def test_generic_child_collection_delegation_is_inconclusive() -> None:
    page = PAGE.replace('{shown.map(w => <article key={w.id}>{w.duration}</article>)}',
                        '<Catalog items={workouts}/>')
    files = candidate(page)
    files['src/app/Catalog.tsx'] = '''import {useState} from "react";
export default function Catalog({items}) {
  const [duration,setDuration]=useState(0);
  return <section><button onClick={()=>setDuration(1)}>До 30 минут</button>
    {items.filter(w=>duration===0 || w.duration<=30).map(w=><article>{w.duration}</article>)}
  </section>;
}'''
    assert max_source_completion_gap(PROMPT, files, portable=True) is None

PRIORITY_PROMPT = "Добавь видимый фильтр по приоритету."
MIXED_PRIORITY_PAGE = '''import {useState} from "react";
export default function Page({orders}) {
  const [filter,setFilter]=useState("all");
  const [priorityFilter,setPriorityFilter]=useState("all");
  const visible=orders.filter(o=>(filter==="all" || o.status===filter) &&
    (priorityFilter==="all" || o.priority===priorityFilter));
  return <ListView orders={visible} filter={filter} onFilter={setFilter}/>;
}
function ListView({orders,filter,onFilter}) {
  return <section><button onClick={()=>onFilter("ready")}>Ready</button>
    {orders.map(o=><article key={o.id}>{o.status}</article>)}</section>;
}'''


@pytest.mark.parametrize("prompt", [PRIORITY_PROMPT, "Add visible priority filter controls."])
def test_working_status_filter_cannot_hide_requested_inert_priority(prompt: str) -> None:
    gap = max_source_completion_gap(prompt, candidate(MIXED_PRIORITY_PAGE), portable=True)
    assert gap is not None and "setPriorityFilter" in gap


@pytest.mark.parametrize("prompt", [
    "Добавь видимый фильтр по статусу.",
    "Добавь видимые фильтры.",
    "Только API: добавь видимый фильтр по приоритету.",
    "Не добавляй видимый фильтр по приоритету.",
    "Добавь видимый фильтр по статусу. Не меняй приоритет.",
])
def test_unrequested_priority_keeps_existing_mixed_filter_acceptance(prompt: str) -> None:
    assert max_source_completion_gap(prompt, candidate(MIXED_PRIORITY_PAGE), portable=True) is None


def test_connected_priority_control_has_no_negative_witness() -> None:
    page = MIXED_PRIORITY_PAGE.replace(
        'return <ListView',
        'return <><button onClick={()=>setPriorityFilter("high")}>High</button><ListView',
    ).replace('onFilter={setFilter}/>;', 'onFilter={setFilter}/></>;')
    assert max_source_completion_gap(PRIORITY_PROMPT, candidate(page), portable=True) is None


@pytest.mark.parametrize("page", [
    MIXED_PRIORITY_PAGE.split('function ListView')[0]
    + '\n// ListView implementation is imported or otherwise unknown',
    MIXED_PRIORITY_PAGE.replace('function ListView', 'function OldListView'),
    MIXED_PRIORITY_PAGE.replace('return <section>',
                               'const shown = customFilter(orders); return <section>'),
    MIXED_PRIORITY_PAGE.replace('{orders.map', '{orders.filter(o=>o.priority==="high").map'),
    MIXED_PRIORITY_PAGE.replace('return <section>',
                               'const p = useSearchParams(); return <section>'),
    MIXED_PRIORITY_PAGE.replace('return <section>', 'return <section><OtherList items={orders}/>'),
    MIXED_PRIORITY_PAGE.replace('{o.status}', '{o[column]}'),
])
def test_unknown_or_alternative_priority_view_remains_inconclusive(page: str) -> None:
    assert max_source_completion_gap(PRIORITY_PROMPT, candidate(page), portable=True) is None

@pytest.mark.parametrize("change", [
    ('return <ListView', 'const [priority,setPriority]=useState("all"); '
     'const alternate=orders.filter(o=>priority==="all" || o.priority===priority); '
     'setPriority("high"); return <ListView'),
    ('function ListView({orders,filter,onFilter})',
     'function ListView({orders:rows,filter,onFilter})'),
])
def test_alternative_priority_state_or_aliased_child_input_is_unknown(change) -> None:
    page = MIXED_PRIORITY_PAGE.replace(*change)
    if 'orders:rows' in page:
        page = page.replace('{orders.map', '{customFilter(rows).map')
    assert max_source_completion_gap(PRIORITY_PROMPT, candidate(page), portable=True) is None


def test_member_prop_alias_with_unknown_filter_is_inconclusive() -> None:
    page = MIXED_PRIORITY_PAGE.replace(
        'function ListView({orders,filter,onFilter}) {',
        'function ListView(props) { const rows=props.orders;',
    ).replace('{orders.map', '{customFilter(rows).map')
    assert max_source_completion_gap(PRIORITY_PROMPT, candidate(page), portable=True) is None


def test_normalized_priority_comparison_still_exposes_inert_setter() -> None:
    page = MIXED_PRIORITY_PAGE.replace('o.priority===priorityFilter',
                                       'normalizePriority(o.priority)===priorityFilter')
    gap = max_source_completion_gap(PRIORITY_PROMPT, candidate(page), portable=True)
    assert gap is not None and 'setPriorityFilter' in gap

@pytest.mark.parametrize("predicate", [
    'priority === "all" || o["priority"] === priority',
    'matchesPriority(o, priority)',
    'priority === "all" || o[field] === priority',
    'priority === "all" || (priority === "high" && o.priority === "high")',
])
def test_active_unknown_priority_filter_after_visible_collection_is_inconclusive(predicate):
    page = MIXED_PRIORITY_PAGE.replace(
        'return <ListView',
        'const [priority,setPriority]=useState("all"); '
        f'const actual=visible.filter(o=>{predicate}); '
        'return <><button onClick={()=>setPriority("high")}>High</button><ListView',
    ).replace('orders={visible}', 'orders={actual}').replace(
        'onFilter={setFilter}/>;', 'onFilter={setFilter}/></>;',
    )
    assert max_source_completion_gap(PRIORITY_PROMPT, candidate(page), portable=True) is None
