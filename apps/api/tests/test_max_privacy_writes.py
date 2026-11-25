"""Storage disclosure must follow product calls and never echo arbitrary customer source."""

from __future__ import annotations

from textwrap import dedent

import pytest

from yleum_api.services.max_privacy_writes import ProductWrites, product_writes


def entry(source: str, **extra: str) -> dict[str, str]:
    return {"src/app/page.tsx": dedent(source), **extra}


def test_reachable_named_sdk_imports_and_explicit_barrel():
    result = product_writes(
        entry(
            """        import { save } from "../business";
        import { createManagedLead as lead } from "@/lib/omnia/business-client";
        import * as commerce from "@/lib/omnia/business-client";
        export default function Page() {
          return <button onClick={() => {
            save("any_action", {nutritionGoal, contactEmail: person.email});
            commerce.createManagedBooking(booking);
            lead(input);
          }}>OK</button>;
        }""",
            **{"src/business.ts": 'export {createMaxAction as save} from "@/lib/omnia/client";'},
        )
    )
    assert result.complete
    assert set(result.server) == {
        "цель питания",
        "контактный email",
        "обращения пользователя",
        "заявки на запись",
    }
    assert not result.browser and not result.session


@pytest.mark.parametrize(
    "source",
    [
        """import {createMaxAction} from "@/lib/omnia/integration-client";
       export default function Page() {return <p>Анкета, план питания, история</p>}""",
        """function createManagedOrder() {}
       export default function Page(){createManagedOrder();return <p/>;}""",
        """import {createMaxAction} from "another";
       export default function Page(){createMaxAction("survey", {});return <p/>;}""",
        """import {createMaxAction as save} from "@/lib/omnia/client";
       function mock(save: Function){save("nutrition", {});}
       export default function Page(){mock(console.log);return <p/>;}""",
        """import {createMaxAction as save} from "@/lib/omnia/client";
       function neverCalled(){save("food", {mealPlan});}
       export default function Page(){return <p/>;}""",
        """import {createMaxAction as save} from "@/lib/omnia/client";
       export default function Page(){if(false){save("unused", {});}return <p/>;}""",
        """import {createMaxAction as save} from "@/lib/omnia/client";
       export default function Page(){return <p/>;save("unused", {});}""",
        """import {getMaxActions} from "@/lib/omnia/client";
       export default function Page(){getMaxActions(); localStorage.getItem('mealPlan');
         sessionStorage.removeItem("theme");return <p/>;}""",
        """export default function Page(localStorage: MapLike){
         localStorage.setItem('mealPlan', 1);return <p/>;}""",
        """export default function Page(){
         const storage={setItem: console.log}; storage.setItem('mealPlan', 'foo');return <p/>;}""",
    ],
)
def test_similar_strings_reads_and_unconnected_writes_are_not_storage_evidence(source):
    files = entry(
        source,
        **{
            "src/unused.ts": "sessionStorage.setItem('secret', input);",
            "src/__tests__/page.tsx": "localStorage.setItem('theme', input);",
            "src/lib/omnia/client.ts": "localStorage.setItem('theme', input);",
        },
    )
    assert product_writes(files) == ProductWrites((), (), (), True)


def test_unknown_values_are_opaque_and_browser_storage_stays_separate():
    result = product_writes(
        entry("""
        import {createMaxAction as log} from "@/lib/omnia/integration-client";
        export default function Page() {
          return <button onClick={() => {
            log("do_not_disclose_free_text", arbitrary);
            localStorage.setItem("theme", "do_not_disclose_free_text");
            window.sessionStorage.setItem("arbitrary-address@example.test", JSON.stringify(input));
          }}>OK</button>;
        }
    """)
    )
    assert result == ProductWrites(
        ("записи о действиях пользователя",),
        ("настройки оформления",),
        ("данные приложения в хранилище браузера",),
        True,
    )


def test_distinct_apps_produce_distinct_storage_labels():
    def data_for(method):
        return product_writes(
            entry(f"""
            import {{ {method} }} from "@/lib/omnia/business-client";
            export default function Page() {{
              return <button onClick={{()=>{method}(input)}} />;
            }}
        """)
        )

    assert data_for("createManagedOrder").server == ("заказы пользователя",)
    assert data_for("createManagedBooking").server == ("заявки на запись",)


def test_unparsed_source_is_not_a_claim_of_absence():
    assert product_writes({}) == ProductWrites((), (), (), False)
    assert not product_writes(entry("export default function ( {")).complete


def test_ambiguous_callback_and_unresolved_default_export_request_owner_review():
    assert not product_writes(
        entry("""
        export default function Page(){
          unknownCallback(()=>fetch('/api/new', {method:"POST"}));return <p/>;
        }
    """)
    ).complete
    assert not product_writes(entry("export {default} from 'unreviewed-ui';")).complete


def test_separately_exported_root_function_is_an_entrypoint():
    assert product_writes(
        entry("""
        function Page(){return <button onClick={()=>window.localStorage.setItem("theme","light")}/>}
        export default Page;
    """)
    ).browser == ("настройки оформления",)


def test_unused_exports_and_a_private_writer_called_only_by_an_unused_export():
    files = entry(
        """
        import { load } from '../data';
        export default function Page(){return <p>{load()}</p>}
    """,
        **{
            "src/data.ts": """
        function store(){localStorage.setItem("meal_history","unused");}
        export function unused(){store();}
        export function load(){return true;}
    """
        },
    )
    assert product_writes(files) == ProductWrites((), (), (), True)


def test_reachable_exported_callback_follows_the_called_writer():
    files = entry(
        """
        import { save as action } from '../data';
        export default function Page(){return <button onClick={action}/>;}
    """,
        **{
            "src/data.ts": """
        export function save(){localStorage.setItem("theme","dark");}
    """
        },
    )
    assert product_writes(files).browser == ("настройки оформления",)


def test_undefined_fields_are_omitted_from_wire_payload():
    result = product_writes(
        entry("""
        import {createMaxAction} from "@/lib/omnia/client";
        export default function Page(){return <button onClick={()=>createMaxAction(
            "edit", {contactEmail: undefined, waterMl: void 0, theme: "dark"}
        )}/>;}
    """)
    )
    assert result == ProductWrites(("настройки оформления",), (), (), True)


@pytest.mark.parametrize(
    "stmt",
    [
        'fetch("/api/profile", {method:"POST", body:JSON.stringify(state)})',
        'window.fetch("/api/profile", options)',
        'indexedDB.open("visits", 1)',
    ],
)
def test_unrecognized_persistence_advises_owner_without_inventing_server_categories(stmt):
    assert product_writes(entry(f"export default function Page(){{{stmt};return <p/>;}}")) == (
        ProductWrites((), (), (), False)
    )


def test_partially_unknown_action_payload_and_managed_inputs():
    result = product_writes(
        entry("""
        import {createMaxAction} from "@/lib/omnia/client";
        import {createManagedLead} from "@/lib/omnia/business-client";
        export default function Page() {return <button onClick={()=>{
          createMaxAction("any", {waterMl, ...state});
          createManagedLead({customerName, contactPhone, comment});
        }}/>;}
    """)
    )
    assert set(result.server) == {
        "количество выпитой воды",
        "записи о действиях пользователя",
        "имя клиента",
        "контактный телефон",
        "обращения пользователя",
    }


def test_reachable_screen_consumer_writes_device_data():
    files = entry(
        """
        import Form from "../form";
        export default function Page(){return <Form/>}
    """,
        **{
            "src/form.tsx": """
        export default function Form(){return <button onClick={()=>
           window.localStorage.setItem("meal_history", JSON.stringify(diary))}/>;}
    """
        },
    )
    assert product_writes(files) == ProductWrites((), ("история приёмов пищи",), (), True)
