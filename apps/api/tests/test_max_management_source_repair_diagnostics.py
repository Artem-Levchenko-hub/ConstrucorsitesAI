"""Repair feedback locates the actual refusal without changing source admission."""

import re

import pytest

from yleum_api.services import max_management_generation as management
from yleum_api.services.max_management_contract import build_management_contract

BASE = """
import {useYleumAppProfile} from '@/lib/omnia/app-profile-client';
import {useYleumCatalog,useYleumCatalogSelection} from '@/lib/omnia/catalog-client';
export default function Page(){
  const {profile}=useYleumAppProfile();const catalog=useYleumCatalog();
  useYleumCatalogSelection(catalog.items);
  const cart:Record<string,number>={sku:2};
  EXTRA
  return <main><h1>{profile?.app_name}</h1>
    {catalog.items.map(item=><p key={item.id}>{item.title}</p>)}
  </main>;
}
"""


def source_gap(extra: str, *, prefix: str = "", **files: str):
    source = prefix + BASE.replace("EXTRA", extra)
    return source, management.management_source_gap({
        ".omnia/management.json": build_management_contract(["catalog"]).model_dump_json(),
        "src/app/page.tsx": source,
        **files,
    })


def location(gap):
    match = re.search(r"Source location: ([^:]+):(\d+):(\d+); AST (\w+); reason (\w+)", gap)
    assert match
    return match.group(1), int(match.group(2)), int(match.group(3)), match.group(4), match.group(5)


@pytest.mark.parametrize("operation", [
    "Object.entries(cart)", "Object.values(cart)", "Object.fromEntries([])",
])
def test_first_native_call_is_located_before_the_later_loop(operation):
    source, gap = source_gap(
        f"const rows={operation};let count=0;"
        "for(const item of catalog.items){count+=1;}",
    )
    assert gap
    offset = source.index(operation)
    row = source[:offset].count("\n") + 1
    column = offset - source.rfind("\n", 0, offset)
    assert location(gap) == (
        "src/app/page.tsx", row, column, "call_expression", "unproven_native_intrinsic_effect",
    )


@pytest.mark.parametrize("statement,call", [
    ("const O=Object;const rows=O.entries(cart);", "O.entries(cart)"),
    ("opaque(Object);", "opaque(Object)"),
    ("opaque(Array);", "opaque(Array)"),
    ("function take(value){opaque(value);}take(Object);", "opaque(value)"),
])
def test_unknown_call_native_escape_keeps_its_actual_invocation_site(statement, call):
    source, gap = source_gap(statement)
    assert gap
    offset = source.index(call)
    assert location(gap)[1:3] == (
        source[:offset].count("\n") + 1, offset - source.rfind("\n", 0, offset),
    )
    assert location(gap)[4] == "unproven_native_intrinsic_effect"


def test_native_rejection_identity_ignores_line_shift_and_irrelevant_later_loop_edits():
    first = "const rows=Object.entries(cart);"
    _, original = source_gap(first + "let n=0;for(const item of catalog.items){n+=1;}")
    _, shifted = source_gap(
        first + "let total=0;for(const row of catalog.items){total+=2;}",
        prefix="// unrelated comment\n\n",
    )
    assert original and shifted
    assert location(shifted)[1] == location(original)[1] + 2
    assert original.rejection_key() == shifted.rejection_key()


@pytest.mark.parametrize("statement,reason", [
    ("Object.entries(cart);", "unproven_native_intrinsic_effect"),
    ("for(const row of catalog.items){if(!row.active)continue;}", "unsupported_loop_control"),
    ("for(const row of catalog.items){const title=row.title;}", "unproven_loop_iterable"),
])
def test_known_shape_feedback_keeps_identity_and_gives_a_bounded_supported_reference(
    statement, reason,
):
    _, gap = source_gap(statement)
    assert gap and reason in gap
    assert "Supported source repair:" in gap
    assert management._SUPPORTED_CART_REPAIR_EXAMPLE in gap
    assert "already proven local JSON" in gap and "existing validated cart schema" in gap
    assert "A DTO is not SDK render authority" in gap
    assert "Preserve the manifest-selected create handler" in gap
    assert "add model/repair budget" in gap
    base = str(gap).split("\nSupported source repair:", 1)[0]
    assert gap.rejection_key() == management.ManagedSourceGap(base).rejection_key()


def test_published_scalar_quantity_cart_reference_passes_the_unchanged_oracle():
    _, gap = source_gap(management._SUPPORTED_CART_REPAIR_EXAMPLE)
    assert gap is None


@pytest.mark.parametrize("move_hook", [False, True])
def test_unproven_generic_storage_writer_gets_a_data_preserving_repair(move_hook):
    react = 'import {useState,useEffect} from "react";\n'
    hook = """
function useStored<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(initial);
  useEffect(() => {
    window.localStorage.setItem(key, JSON.stringify(value));
  }, [key, value]);
  return [value, setValue] as const;
}
"""
    files = {"src/lib/use-stored.ts": react + "export " + hook} if move_hook else {}
    prefix = 'import {useStored} from "@/lib/use-stored";\n' if move_hook else react + hook
    _, gap = source_gap(
        'const [draft, setDraft] = useStored("fixture-cart.v1", unknownInitial());',
        prefix=prefix, **files,
    )
    assert gap
    expected_path = "src/lib/use-stored.ts" if move_hook else "src/app/page.tsx"
    path, _, _, _, reason = location(gap)
    assert (path, reason) == (expected_path, "unproven_json_stringify")
    base, _, guidance = gap.partition("\n")
    assert gap.rejection_key() == management.ManagedSourceGap(base).rejection_key()
    assert "explicit typed state" in guidance
    assert "existing keys, stored shapes and writes" in guidance
    assert "wait for hydration" in guidance
    assert "moving the generic helper" in guidance
    assert "fixture-cart.v1" not in gap and len(gap.encode("utf-8")) < 1200


def test_explicit_hydrated_local_json_writes_keep_managed_connections_proven():
    _, gap = source_gap(
        """
const [cartDraft, setCartDraft] = useState({quantity: 0});
const [favorite, setFavorite] = useState<string | null>(null);
const [hydrated, setHydrated] = useState(false);
useEffect(() => {
  try {
    const storage = window.localStorage;
    const savedCart = storage.getItem("fixture-cart.v1");
    const savedFavorite = storage.getItem("fixture-favorite.v1");
    if (savedCart) {
      try {
        const parsed = JSON.parse(savedCart);
        if (parsed !== null && typeof parsed.quantity === "number") {
          setCartDraft({quantity: parsed.quantity});
        }
      } catch {}
    }
    if (savedFavorite) {
      try {
        const parsed = JSON.parse(savedFavorite);
        if (typeof parsed === "string") setFavorite(parsed);
      } catch {}
    }
    setHydrated(true);
  } catch {}
}, []);
useEffect(() => {
  if (!hydrated) return;
  try {
    const storage = window.localStorage;
    storage.setItem("fixture-cart.v1", JSON.stringify(cartDraft));
    storage.setItem("fixture-favorite.v1", JSON.stringify(favorite));
  } catch {}
}, [cartDraft, favorite, hydrated]);
""",
        prefix='import {useState,useEffect} from "react";\n',
    )
    assert gap is None


@pytest.mark.parametrize("statement", [
    "const cartRows=Object.keys(cart).map(id=>{catalog.items.length=0;return{id,qty:1};});",
    "const cartRows=Object.keys(cart).map(id=>{"
    "const selected=catalog.items.find(item=>item.id===id)??null;"
    "selected.title='fake';return{id,qty:1};});",
    "Reflect.defineProperty(Reflect.getPrototypeOf({}),'toJSON',"
    "{value:()=>{catalog.items.length=0;return {};}});JSON.stringify({});",
    "Object.prototype.toJSON=()=>({});JSON.stringify({});",
])
def test_repair_guidance_does_not_admit_mutations_or_reflection(statement):
    _, gap = source_gap(statement)
    assert gap is not None


def test_diagnostic_and_reference_never_copy_source_values():
    _, gap = source_gap("Object.entries({api_key:'synthetic-private-value'});")
    assert gap
    assert "synthetic-private-value" not in gap and "api_key" not in gap
    assert len(gap.encode()) < 2600


def test_guidance_does_not_grant_derived_dto_sdk_render_identity():
    source = BASE.replace("EXTRA", management._SUPPORTED_CART_REPAIR_EXAMPLE).replace(
        "catalog.items.map(item=><p key={item.id}>{item.title}</p>)",
        "cartRows.map(row=><p key={row.id}>{row.title}</p>)",
    )
    gap = management.management_source_gap({
        ".omnia/management.json": build_management_contract(["catalog"]).model_dump_json(),
        "src/app/page.tsx": source,
    })
    assert gap and gap.startswith("Connect useYleumCatalog")
