"""Passive collection aliases never become rendered SDK identity or hide effects."""

import pytest

from yleum_api.services.max_management_contract import build_management_contract
from yleum_api.services.max_management_generation import management_source_gap


def source_gap(statements: str, *, rendered: str | None = None):
    output = rendered or (
        "catalog.items.map(item => <article key={item.id}>{item.title}</article>)"
    )
    source = """
import {useCallback,useEffect,useState} from 'react';
import {useYleumAppProfile} from '@/lib/omnia/app-profile-client';
import {useYleumCatalog,useYleumCatalogSelection} from '@/lib/omnia/catalog-client';
export default function Page(){
  const {profile}=useYleumAppProfile();
  const catalog=useYleumCatalog();
  useYleumCatalogSelection(catalog.items);
  STATEMENTS
  return <main><h1>{profile?.app_name}</h1>{OUTPUT}</main>;
}
""".replace("STATEMENTS", statements).replace("OUTPUT", output)
    return management_source_gap({
        ".omnia/management.json": build_management_contract(["catalog"]).model_dump_json(),
        "src/app/page.tsx": source,
    })


@pytest.mark.parametrize("statistics", [
    "const active=catalog.items.filter(item=>item.active);",
    "const active=catalog.items.filter(item=>item.active);"
    "const selected=active.filter(item=>item.category==='drinks');",
    "const active=catalog.items.filter(item=>item.active);"
    "const selected=active.filter(item=>item.category==='drinks');"
    "const total=selected.reduce((sum,item)=>sum+1,0);",
])
def test_passive_filter_chains_preserve_separate_direct_catalog(statistics):
    assert source_gap(statistics) is None


@pytest.mark.parametrize("statement", [
    'const comment=["message", ""].filter(Boolean).join(" ");',
    'const comment=["message", "", null, false, 0, 1].filter(Boolean).join(" ");',
    'const comment=[].filter(Boolean).join(" ");',
    'const comment=["message", ""].filter(Boolean).filter(text=>text!=="").join(" ");',
])
def test_unshadowed_boolean_filters_known_local_scalars(statement):
    assert source_gap(statement) is None


@pytest.mark.parametrize("tail", [
    "const selected=active.filter(item=>opaque(item));",
    "const selected=active.filter(item=>{item.title='fake';return true;});",
    "const selected=active.filter(item=>{catalog.items.length=0;return true;});",
    "const selected=active.filter(async item=>item.active);",
    "const selected=active.filter((item=opaque(catalog.items))=>true);",
    "const selected=active.filter((...items)=>true);",
    "const selected=active.filter((item,index,all)=>all.splice(0));",
    "const selected=active.filter(item=>unknownObject.active);",
    "const selected=active.filter(item=>item.active);opaque(selected);",
    "active.length=0;",
])
def test_filter_alias_effects_still_invalidate_original_catalog(tail):
    assert source_gap("const active=catalog.items.filter(item=>item.active);" + tail)


@pytest.mark.parametrize("statement", [
    "const Boolean=opaque;const selected=['message'].filter(Boolean);",
    "function Boolean(value){catalog.items.length=0;return value;}"
    "const selected=['message'].filter(Boolean);",
    "const selected=[unknown].filter(Boolean);",
    "const selected=[{get active(){catalog.items.length=0;return true;}}].filter(Boolean);",
    "const selected=catalog.items.filter(Boolean);",
    "const active=catalog.items.filter(item=>item.active);const selected=active.filter(Boolean);",
    "const empty=[];const selected=empty.filter(opaque);",
])
def test_boolean_intrinsic_never_trusts_unknown_objects_callbacks_or_sdk_rows(statement):
    assert source_gap(statement)


@pytest.mark.parametrize("rendered", [
    "active.map(item=><article key={item.id}>{item.title}</article>)",
    "selected.map(item=><article key={item.id}>{item.title}</article>)",
])
def test_passive_filter_results_never_earn_rendered_sdk_row_identity(rendered):
    gap = source_gap(
        "const active=catalog.items.filter(item=>item.active);"
        "const selected=active.filter(item=>item.active);",
        rendered=rendered,
    )
    assert gap and gap.startswith("Connect useYleumCatalog")


def test_b_style_cart_indexed_predicate_remains_explicitly_unproved():
    gap = source_gap(
        "const cart={};const activeItems=catalog.items.filter(item=>item.active);"
        "const cartLines=activeItems.filter(item=>(cart[item.id]??0)>0);",
    )
    assert gap and "unproven_statistic_callback" in gap


FILTER_CHAIN = (
    "const active=catalog.items.filter(item=>item.active);"
    "const selected=active.filter(item=>item.category==='drinks');"
)


@pytest.mark.parametrize("statement", [
    "const [rows,setRows]=useState(selected);",
    "const [rows,setRows]=useState([]);setRows(selected);",
    "const [rows,setRows]=useState(selected);setRows([]);",
    "const rows={...selected};const copied={...rows};",
])
def test_unmutated_state_and_spread_aliases_preserve_direct_catalog(statement):
    assert source_gap(FILTER_CHAIN + statement) is None


@pytest.mark.parametrize("statement", [
    "const [rows,setRows]=useState(selected);rows[0].title='fake';",
    "const [rows,setRows]=useState([]);setRows(selected);rows[0].title='fake';",
    "const [rows,setRows]=useState(selected);setRows([]);rows[0].title='fake';",
    "const [rows,setRows]=useState(selected);const oldRows=rows;"
    "setRows([]);oldRows[0].title='fake';",
    "const [rows,setRows]=useState(selected);opaque(rows);",
    "const [rows]=[selected];rows[0].title='fake';",
    "const {rows}={rows:selected};rows[0].title='fake';",
    "const rows={...selected};rows[0].title='fake';",
    "const rows={...selected};const copied={...rows};copied[0].title='fake';",
    "const rows={...{rows:selected}};rows.rows[0].title='fake';",
    "const [rows,setRows]=useState({...selected});rows[0].title='fake';",
    "const rows=[...selected];rows[0].title='fake';",
    "async function load(){return selected;}"
    "load().then(rows=>{rows[0].title='fake';});",
    "Promise.all([selected]).then(([rows])=>{rows[0].title='fake';});",
    "const getRows=useCallback(()=>selected,[]);const rows=getRows();rows[0].title='fake';",
])
def test_filter_alias_copy_paths_never_drop_mutation_authority(statement):
    assert source_gap(FILTER_CHAIN + statement)


def test_filter_state_alias_still_cannot_supply_rendered_sdk_identity():
    gap = source_gap(
        FILTER_CHAIN + "const [rows,setRows]=useState(selected);",
        rendered="rows.map(item=><article key={item.id}>{item.title}</article>)",
    )
    assert gap and gap.startswith("Connect useYleumCatalog")


@pytest.mark.parametrize("statement", [
    "const [rows]=useState(()=>[]);",
    "const [rows]=useState(()=>selected);",
    "const [rows,setRows]=useState(selected);setRows(previous=>previous);",
    "const [rows,setRows]=useState([]);setRows(()=>selected);",
    "const [rows]=useState(()=>{function unused(){return new Unknown();}return [];});",
])
def test_proven_state_callbacks_keep_separate_direct_catalog(statement):
    assert source_gap(FILTER_CHAIN + statement) is None


@pytest.mark.parametrize("statement", [
    "const [rows]=useState(()=>{catalog.items.length=0;return [];});",
    "const [rows]=useState(()=>selected);rows[0].title='fake';",
    "const [rows,setRows]=useState([]);setRows(()=>selected);rows[0].title='fake';",
    "const [rows,setRows]=useState(selected);"
    "setRows(previous=>{previous[0].title='fake';return previous;});",
    "const [rows,setRows]=useState([]);"
    "setRows(()=>{catalog.items.length=0;return [];});",
    "const [rows]=useState(()=>{if(flag)return [];else catalog.items.length=0;return [];});",
    "function update(){if(flag)return [];else catalog.items.length=0;return [];}"
    "const [rows]=useState(()=>update());",
    "const [rows]=useState(()=>{if(flag)return [];else return selected;});"
    "rows[0].title='fake';",
    "function load(){if(flag)return [];else return selected;}"
    "const [rows]=useState(()=>load());rows[0].title='fake';",
    "const [rows]=useState(async()=>selected);",
    "const [rows]=useState((row=opaque(catalog.items))=>[]);",
    "const [rows]=useState((...rows)=>[]);",
    "const [rows,setRows]=useState([]);setRows((...rows)=>selected);",
    "const [rows]=useState(()=>{try{return [];}catch{catalog.items.length=0;return [];}});",
    "const [rows]=useState(()=>new Unknown());",
    "const [rows]=useState(()=>()=>selected);",
    "function initialize(){return initialize();}const [rows]=useState(initialize);",
])
def test_executed_state_callbacks_cannot_hide_sdk_effects_or_return_aliases(statement):
    assert source_gap(FILTER_CHAIN + statement)


@pytest.mark.parametrize("statement", [
    "const [rows]=useState(()=>selected);",
    "const [rows,setRows]=useState([]);setRows(()=>selected);",
])
def test_lazy_or_updated_filter_state_still_earns_no_derived_render_witness(statement):
    assert source_gap(
        FILTER_CHAIN + statement,
        rendered="rows.map(item=><p key={item.id}>{item.title}</p>)",
    )


JSON_CART = """
const [cart,setCart]=useState({});
useEffect(()=>{
  const raw=window.localStorage.getItem('cart');
  if(raw)setCart(JSON.parse(raw));
},[]);
useEffect(()=>{window.localStorage.setItem('cart',JSON.stringify(cart));},[cart]);
setCart(previous=>{const next={...previous};return next;});
"""


def test_guarded_storage_json_and_pure_current_render_updater_are_supported():
    assert source_gap(JSON_CART) is None


@pytest.mark.parametrize("statement", [
    "const local=JSON.parse('{}');JSON.stringify(local);",
    "const local=JSON.parse('[1,true,null]');JSON.stringify(local);",
    "const local=JSON.parse(JSON.stringify({title:'local',count:1}));",
])
def test_native_plain_json_operations_do_not_supply_sdk_authority(statement):
    assert source_gap(statement) is None


@pytest.mark.parametrize("mutation", [
    "const JSON={parse:()=>({}),stringify:()=>''};",
    "const window={localStorage:{getItem:()=>'{\"x\":1}',setItem:()=>{}}};",
    "JSON.parse=()=>({});",
    "JSON.stringify=()=>'';",
    "window.localStorage.getItem=()=>'{\"x\":1}';",
    "window.localStorage={getItem:()=>'{\"x\":1}'};",
])
def test_shadowed_or_modified_native_json_storage_is_not_trusted(mutation):
    assert source_gap(mutation + JSON_CART)


@pytest.mark.parametrize("statement", [
    "const localStorage={getItem:()=>'{\"x\":1}'};localStorage.getItem('cart');",
    "window.localStorage.getItem(unknown);",
    "window.localStorage.getItem('cart',unknown);",
    "const raw=window.localStorage.getItem('cart');JSON.parse(raw);",
    "JSON.parse(unknown);",
    "JSON.parse('{}',function reviver(key,value){catalog.items.length=0;return value;});",
    "JSON.stringify(catalog.items);",
    "JSON.stringify({row:catalog.items});",
    "JSON.stringify({unknown});",
    "JSON.stringify({toJSON(){catalog.items.length=0;return {};}});",
    "JSON.stringify({get value(){catalog.items.length=0;return 1;}});",
    "JSON.stringify({value:()=>catalog.items});",
    "JSON.stringify({},function replacer(key,value){catalog.items.length=0;return value;});",
])
def test_json_storage_intrinsics_deny_unknown_coercion_callbacks_and_sdk_data(statement):
    assert source_gap(statement)


KEY_LOOKUP = """
const cart={};
const rows=Object.keys(cart).map(id=>{
  const found=catalog.items.find(item=>item.id===id)??null;
  return {title:found?found.title:''};
});
"""


def test_known_plain_json_keys_are_scalar_effects_only_lookup_inputs():
    assert source_gap(KEY_LOOKUP) is None


def test_unknown_function_parameter_cannot_borrow_native_key_identity():
    assert source_gap("""
function lookup(id){
  const found=catalog.items.find(item=>item.id===id)??null;
  return found?found.title:'';
}
lookup(unknown);
""")


@pytest.mark.parametrize("statement", [
    "const Object={keys:()=>['fake']};Object.keys({});",
    "Object.keys=()=>['fake'];Object.keys({});",
    "Object.keys(unknown);",
    "Object.keys(catalog.items);",
    "Object.keys({row:catalog.items});",
    "Object.keys({get value(){catalog.items.length=0;return 1;}});",
    "Object.keys({},unknown);",
    "Object.keys({}).map(unknown);",
    "Object.keys({}).map(async key=>key);",
    "Object.keys({}).map((key=opaque(catalog.items))=>key);",
    "Object.keys({}).map((...keys)=>keys);",
    "Object.keys({}).map(key=>{catalog.items.length=0;return key;});",
    "Object.keys({}).map(key=>{if(flag)return key;else catalog.items.length=0;return key;});",
    "function mapKey(key){if(flag)return key;else catalog.items.length=0;return key;}"
    "Object.keys({}).map(mapKey);",
    "const rows=Object.keys({}).map(key=>catalog.items);rows[0].length=0;",
    "const rows=Object.keys({}).map(key=>catalog.items[0]);rows[0].title='fake';",
    "Object.keys({}).map(id=>{id=unknown;"
    "const found=catalog.items.find(item=>item.id===id)??null;return found?found.title:'';});",
    "Object.keys({}).map(id=>{"
    "const found=catalog.items.find(item=>item.id===id)??null;"
    "found.title='fake';return '';});",
])
def test_native_key_arrays_cannot_hide_unknown_effects_or_borrow_sdk_identity(statement):
    assert source_gap(statement)


def test_native_keys_and_read_only_lookup_results_are_not_catalog_render_witnesses():
    assert source_gap(
        KEY_LOOKUP,
        rendered="rows.map(row=><p>{row.title}</p>)",
    )


@pytest.mark.parametrize("statement", [
    "const [rows]=useState(()=>{if(1<=0)return [];else return [];});",
    "const [rows,setRows]=useState({});"
    "setRows(previous=>{const next={...previous};if(1<=0)delete next.id;"
    "else next.id=1;return next;});",
])
def test_finite_callback_numeric_guards_allow_effects_proof_without_extra_witnesses(statement):
    assert source_gap(statement) is None


@pytest.mark.parametrize("statement", [
    "const [rows]=useState(()=>{if(1<=0)catalog.items.length=0;return [];});",
    "const [rows]=useState(()=>{if(1<=0)return [];else catalog.items.length=0;return [];});",
    "const [rows]=useState(()=>{if('1'<=0)return [];else return [];});",
    "const [rows]=useState(()=>{if(true<=false)return [];else return [];});",
    "const value={valueOf(){catalog.items.length=0;return 1;}};"
    "const [rows]=useState(()=>{if(value<=0)return [];else return [];});",
    "if(1<=0)catalog.items.length=0;",
])
def test_callback_numeric_guard_scope_never_hides_either_sdk_effect_branch(statement):
    assert source_gap(statement)


@pytest.mark.parametrize("statement", [
    "Object.prototype.toJSON=()=>{catalog.items.length=0;return {};};JSON.stringify({});",
    "Object.prototype.toJSON=()=>{catalog.items.length=0;return {};};JSON.stringify([{}]);",
    "Array.prototype.toJSON=()=>{catalog.items.length=0;return [];};JSON.stringify([]);",
    "const O=Object;const prototype=O.prototype;"
    "prototype.toJSON=()=>{catalog.items.length=0;return {};};JSON.stringify({});",
    "const A=Array;const prototype=A.prototype;"
    "prototype.toJSON=()=>{catalog.items.length=0;return [];};JSON.stringify([]);",
    "Object['prototype']['toJSON']=()=>({});JSON.stringify({});",
    "const key='prototype';const prototype=Object[key];"
    "prototype.toJSON=()=>({});JSON.stringify({});",
    "const prototype=Object[unknown];prototype[unknown]=()=>({});JSON.stringify({});",
    "const prototype=Array[unknown];prototype[unknown]=()=>[];JSON.stringify([]);",
    "const {prototype}=Object;prototype.toJSON=()=>({});JSON.stringify({});",
    "const Constructor=[].constructor;Constructor.prototype.toJSON=()=>[];JSON.stringify([]);",
    "const Constructor=({}).constructor;Constructor.prototype.toJSON=()=>({});JSON.stringify({});",
    "const prototype=({}).__proto__;prototype.toJSON=()=>({});JSON.stringify({});",
    "const Constructor=[]['con'+'structor'];const prototype=Constructor['pro'+'totype'];"
    "const method='to'+'JSON';prototype[method]=()=>{catalog.items.length=0;return [];};"
    "JSON.stringify([]);",
    "Object.getPrototypeOf({});JSON.stringify({});",
    "Object.defineProperty(Object.prototype,'toJSON',{value:()=>({})});JSON.stringify({});",
    "Object.setPrototypeOf({},unknown);JSON.stringify({});",
    "Array.from([]);JSON.stringify([]);",
    "const O=Object;opaque(O);JSON.stringify({});",
    "const A=Array;opaque(A);JSON.stringify([]);",
])
def test_native_json_cannot_trust_prototype_handles_or_unknown_intrinsic_effects(statement):
    assert source_gap(statement)


def test_native_keys_and_storage_json_stay_supported_without_prototype_operations():
    assert source_gap(JSON_CART + "const keys=Object.keys({qty:1});keys.map(key=>key);") is None


@pytest.mark.parametrize("statement", [
    "Reflect.defineProperty(Reflect.getPrototypeOf({}),'toJSON',"
    "{value:()=>{catalog.items.length=0;return {};}});JSON.stringify({});",
    "const R=Reflect;R.defineProperty(R.getPrototypeOf([]),'toJSON',"
    "{value:()=>{catalog.items.length=0;return [];}});JSON.stringify([]);",
    "const getPrototypeOf=Reflect.getPrototypeOf;const define=Reflect.defineProperty;"
    "define(getPrototypeOf({}),'toJSON',{value:()=>({})});JSON.stringify({});",
    "opaque(Reflect);JSON.stringify({});",
    "window.Reflect.defineProperty(window.Reflect.getPrototypeOf({}),'toJSON',"
    "{value:()=>({})});JSON.stringify({});",
    "globalThis.Reflect.defineProperty(globalThis.Reflect.getPrototypeOf({}),'toJSON',"
    "{value:()=>({})});JSON.stringify({});",
    "const namespace=globalThis;const R=namespace.Reflect;"
    "R.defineProperty(R.getPrototypeOf({}),'toJSON',{value:()=>({})});JSON.stringify({});",
    "const key='Reflect';const R=window[key];"
    "R.defineProperty(R.getPrototypeOf({}),'toJSON',{value:()=>({})});JSON.stringify({});",
    "const key='Reflect';const R=globalThis[key];"
    "R.defineProperty(R.getPrototypeOf({}),'toJSON',{value:()=>({})});JSON.stringify({});",
    "const namespace=window;const R=namespace[unknown];"
    "R.defineProperty(R.getPrototypeOf({}),'toJSON',{value:()=>({})});JSON.stringify({});",
    "const namespace=globalThis;opaque(namespace);JSON.stringify({});",
])
def test_reflection_handles_and_qualified_aliases_cannot_poison_native_json(statement):
    assert source_gap(statement)


@pytest.mark.parametrize("binding", [
    "const O=globalThis.Object;",
    "const O=window.Object;",
    "const namespace=globalThis;const O=namespace.Object;",
    "const namespace=window;const O=namespace.Object;",
    "const [namespace]=useState(globalThis);const O=namespace.Object;",
    "const [namespace]=useState(window);const O=namespace.Object;",
    "const [namespace,setNamespace]=useState(window);"
    "setNamespace({});const O=namespace.Object;",
    "function namespace(){return window;}const O=namespace().Object;",
])
def test_native_namespace_property_escape_does_not_drop_refusal_provenance(binding):
    assert source_gap(
        binding + "O.defineProperty(O.getPrototypeOf({}),'toJSON',"
        "{value:()=>{catalog.items.length=0;return {};}});JSON.stringify({});",
    )


@pytest.mark.parametrize("binding", [
    "const namespace=window;",
    "const [namespace]=useState(window);",
])
def test_only_known_window_storage_child_is_available_through_namespace_aliases(binding):
    assert source_gap(
        binding + "const raw=namespace.localStorage.getItem('cart');if(raw)JSON.parse(raw);",
    ) is None
