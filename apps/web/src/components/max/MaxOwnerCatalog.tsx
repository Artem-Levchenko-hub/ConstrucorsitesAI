"use client";

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, EyeOff, ImagePlus, Loader2, PackageOpen, Pencil, Plus, RefreshCw, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ApiError } from "@/lib/api/client";
import { getMaxProjectConfig, getMaxProjectContent, saveMaxProjectContent } from "@/lib/api/max-studio";
import type { MaxContentDelivery, MaxContentItem, MaxProjectConfig } from "@/lib/api/types";
import { catalogDraft, newMaxContentItem, rebaseCatalogDraft, type MaxCatalogDraft } from "@/lib/max-catalog";
import { MaxContentItemFields } from "./MaxContentItemEditor";
import { MaxProjectDataApplyDialog } from "./MaxProjectDataApplyDialog";
import { MaxSectionShell } from "./MaxSectionShell";
import "./max-project-setup.css";
import "./max-owner-catalog.css";

const availability = { in_stock: "В наличии", on_request: "Под заказ", out_of_stock: "Нет в наличии" };
type CatalogSave = MaxCatalogDraft & { reapply?: boolean };
const deliveryCopy: Record<MaxContentDelivery["state"], string> = {
  applied: "Обновлено", pending: "Обновляется", unknown: "Обновление не подтверждено",
  not_published: "Ещё не опубликовано", blocked: "Обновление приостановлено", deferred: "Обновится после пробуждения", not_created: "Ещё не создан",
};
function errorCopy(error: unknown) { return error instanceof ApiError ? error.message : "Не удалось выполнить запрос. Повторите попытку."; }
function DeliveryStatus({ label, status }: { label: string; status: MaxContentDelivery }) {
  return <div className="max-catalog-delivery" data-state={status.state}><span>{label}</span><strong>{deliveryCopy[status.state]}</strong></div>;
}

export function MaxOwnerCatalog({ projectId, projectName }: { projectId: string; projectName: string }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<MaxCatalogDraft | null>(null);
  const [editor, setEditor] = useState<{ id: string; initial: MaxContentItem; baseline: MaxCatalogDraft } | null>(null);
  const [query, setQuery] = useState("");
  const [visibility, setVisibility] = useState("all");
  const [category, setCategory] = useState("");
  const [uploading, setUploading] = useState(false);
  const [applyConfig, setApplyConfig] = useState<MaxProjectConfig | null>(null);
  const [connectError, setConnectError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const saving = useRef(false);
  const connectTrigger = useRef<HTMLButtonElement>(null);
  const save = useMutation({
    onMutate: () => qc.cancelQueries({ queryKey: ["max-content", projectId] }),
    mutationFn: (value: CatalogSave) => saveMaxProjectContent(projectId, value.version, value.content),
    onSuccess: async (data, value) => {
      // Also cancel a read queued after onMutate: it must not replace this receipt.
      await qc.cancelQueries({ queryKey: ["max-content", projectId] });
      qc.setQueryData(["max-content", projectId], data);
      void qc.invalidateQueries({ queryKey: ["max-config", projectId] });
      if (!value.reapply) { setDraft(null); setEditor(null); setReviewed(false); }
    },
  });
  const content = useQuery({
    queryKey: ["max-content", projectId], queryFn: () => getMaxProjectContent(projectId), retry: false,
    enabled: !save.isPending,
    refetchInterval: state => state.state.data && [state.state.data.public.state, state.state.data.preview.state].some(value => value === "pending" || value === "unknown") ? 5_000 : false,
  });
  const items = draft?.content ?? content.data?.content ?? [];
  // The displayed form and its baseline must describe the same snapshot.
  const selected = editor ? draft?.content.find(item => item.id === editor.id) ?? editor.initial : null;
  const conflict = save.error instanceof ApiError && save.error.status === 409;
  const deliveryPending = content.data && [content.data.public.state, content.data.preview.state].some(state => state === "pending" || state === "unknown");
  const categories = [...new Set(items.map(item => item.category).filter(Boolean))].sort();
  if (category && !categories.includes(category)) setCategory("");
  const filtered = items.filter(item => (visibility === "all" || item.active === (visibility === "visible"))
    && (!category || item.category === category)
    && `${item.title} ${item.description} ${item.category}`.toLocaleLowerCase("ru").includes(query.toLocaleLowerCase("ru")));

  function editItem(item: MaxContentItem) {
    if (!content.data) return;
    setEditor({ id: item.id, initial: item, baseline: draft ?? catalogDraft(content.data) });
  }
  function patchItem(patch: Partial<MaxContentItem>) {
    if (!editor) return;
    setReviewed(false);
    setDraft(previous => {
      const base = previous ?? editor.baseline;
      const exists = base.content.some(item => item.id === editor.id);
      return { ...base, content: exists ? base.content.map(item => item.id === editor.id ? { ...item, ...patch } : item) : [...base.content, { ...editor.initial, ...patch }] };
    });
  }
  function toggleVisibility(item: MaxContentItem) {
    if (!content.data) return;
    setDraft(previous => {
      const base = previous ?? catalogDraft(content.data);
      return { ...base, content: base.content.map(current => current.id === item.id ? { ...current, active: !current.active } : current) };
    });
  }
  function cancelNewItem() {
    if (!editor || editor.baseline.base.some(item => item.id === editor.id)) return;
    setDraft(previous => {
      if (!previous) return null;
      const remaining = previous.content.filter(item => item.id !== editor.id);
      return JSON.stringify(remaining) === JSON.stringify(previous.base) ? null : { ...previous, content: remaining };
    });
    setEditor(null);
  }
  async function persist(value: CatalogSave | null = draft) {
    if (!value || saving.current || uploading) return;
    saving.current = true;
    try { await save.mutateAsync(value); } catch { /* Saved content or the dirty draft retain their original version. */ }
    finally { saving.current = false; }
  }
  async function reapplySaved() {
    if (!content.data || draft || editor || uploading || save.isPending || connecting || applyConfig) return;
    await persist({ ...catalogDraft(content.data), reapply: true });
  }
  async function reviewLatest() {
    const latest = await content.refetch();
    if (!latest.data || latest.error) return;
    const saved = latest.data;
    // Keep edits made while the GET was in flight too.
    if (draft) {
      setDraft(current => current ? rebaseCatalogDraft(current, saved) : null);
      setReviewed(true);
    }
    save.reset();
  }
  async function connectCatalog() {
    if (connecting) return;
    setConnecting(true); setConnectError(null);
    try { setApplyConfig(await getMaxProjectConfig(projectId)); }
    catch (error) { setConnectError(errorCopy(error)); }
    finally { setConnecting(false); }
  }

  return <MaxSectionShell projectId={projectId} projectName={projectName} active="catalog" className="max-catalog-workspace" eyebrow="Каталог" title="Товары и услуги" lead="Меняйте позиции, цены и наличие. Обычное сохранение не расходует баланс ИИ.">
    <section className="max-owner-catalog" aria-label="Каталог приложения">
      {content.data && <div className="max-catalog-status" role="status">
        <div className="max-catalog-delivery" data-state="saved"><span>Данные в Yleum</span><strong>{draft ? "Есть несохранённые правки" : "Сохранены"}</strong></div>
        <DeliveryStatus label="Публичное приложение" status={content.data.public} />
        <DeliveryStatus label="Предпросмотр" status={content.data.preview} />
      </div>}
      {deliveryPending && <div className="max-catalog-notice"><p>Данные сохранены в Yleum. Обновление приложения ещё не подтверждено.</p><Button variant="outline" disabled={Boolean(draft) || Boolean(editor) || uploading || save.isPending || connecting || Boolean(applyConfig)} onClick={() => void reapplySaved()}>{save.isPending && save.variables?.reapply ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}Повторить применение</Button></div>}
      {content.data?.public.reason === "catalog_integration_required" && <div className="max-catalog-notice">
        <div><strong>Подключите каталог к приложению один раз</strong><p>Позиции уже можно редактировать. Чтобы они появились на экранах, нужна доработка ИИ и публикация. Доработка расходует баланс.</p></div>
        <Button ref={connectTrigger} variant="outline" disabled={connecting || Boolean(draft) || save.isPending} onClick={() => void connectCatalog()}>{connecting && <Loader2 className="size-4 animate-spin" />}Подключить каталог</Button>
      </div>}
      {connectError && <p role="alert" className="max-catalog-error">{connectError}</p>}
      {content.isError && <div role="alert" className="max-catalog-notice"><p>{content.data ? "Не удалось проверить актуальный каталог. Показаны последние полученные данные." : "Не удалось загрузить каталог."}</p><Button variant="outline" onClick={() => void content.refetch()}>Повторить</Button></div>}
      {content.isPending ? <p className="max-catalog-empty" role="status"><Loader2 className="size-5 animate-spin" />Загружаем каталог…</p> : content.data && <>
        <div className="max-catalog-toolbar">
          <label className="max-catalog-search"><Search className="size-4" aria-hidden="true" /><Input aria-label="Поиск по каталогу" placeholder="Найти товар или услугу" value={query} onChange={event => setQuery(event.target.value)} /></label>
          <select aria-label="Видимость позиций" value={visibility} onChange={event => setVisibility(event.target.value)}><option value="all">Все позиции</option><option value="visible">Видимые</option><option value="hidden">Скрытые</option></select>
          {categories.length > 0 && <select aria-label="Раздел каталога" value={category} onChange={event => setCategory(event.target.value)}><option value="">Все разделы</option>{categories.map(value => <option key={value}>{value}</option>)}</select>}
          <Button disabled={save.isPending || items.length >= 100} onClick={() => { const item = newMaxContentItem(); editItem(item); }}><Plus className="size-4" />Добавить позицию</Button>
        </div>
        {reviewed && <p className="max-catalog-notice" role="status">Актуальные данные загружены, ваши правки перенесены поверх них. Если одно поле меняли одновременно, оставлен ваш вариант. Проверьте карточки перед сохранением.</p>}
        {save.error && <div className="max-catalog-notice max-catalog-error" role="alert"><div><strong>{conflict ? "Каталог изменился в другой вкладке" : save.variables?.reapply ? "Не удалось повторить применение" : "Не удалось сохранить"}</strong><p>{conflict ? draft ? "Ваш черновик остался в форме. Загрузите актуальные данные и перенесите свои правки, затем проверьте карточки." : "Данные в Yleum сохранены. Обновите каталог перед повторным применением." : errorCopy(save.error)}</p></div>{conflict && <Button variant="outline" disabled={content.isFetching || uploading || save.isPending} onClick={() => void reviewLatest()}><RefreshCw className="size-4" />{draft ? "Перенести мои правки" : "Обновить каталог"}</Button>}</div>}
        {filtered.length ? <div className="max-catalog-grid">{filtered.map(item => <article className="max-catalog-card" key={item.id} data-hidden={!item.active}>
          {item.image_url ? (
            // Owner uploads have arbitrary public hosts; keep the native image loader.
            // eslint-disable-next-line @next/next/no-img-element
            <img src={item.image_url} alt="" loading="lazy" className="max-catalog-photo" />
          ) : <div className="max-catalog-photo max-catalog-photo-empty"><ImagePlus aria-hidden="true" /></div>}
          <div className="max-catalog-card-body"><div className="max-catalog-meta"><span>{item.category || "Без раздела"}</span><span>{item.active ? availability[item.availability] : "Скрыто"}</span></div><h2>{item.title || "Новая позиция"}</h2>{item.description && <p>{item.description}</p>}<strong className="max-catalog-price">{item.price || "Цена не указана"}</strong>
          <div className="max-catalog-card-actions"><Button variant="outline" disabled={save.isPending} onClick={() => editItem(item)}><Pencil className="size-4" />Изменить</Button><Button variant="ghost" disabled={save.isPending} aria-label={`${item.active ? "Скрыть" : "Показать"} ${item.title}`} onClick={() => toggleVisibility(item)}>{item.active ? <EyeOff className="size-4" /> : <Eye className="size-4" />}{item.active ? "Скрыть" : "Показать"}</Button></div></div>
        </article>)}</div> : <div className="max-catalog-empty"><PackageOpen aria-hidden="true" /><h2>{items.length ? "Ничего не найдено" : "Добавьте первый товар или услугу"}</h2><p>{items.length ? "Попробуйте другой запрос или фильтр." : "Название, фото, цена и наличие — всё в одной карточке."}</p></div>}
        {draft && <div className="max-catalog-savebar"><p>Правки ещё не сохранены</p><Button variant="outline" disabled={save.isPending || uploading} onClick={() => { setDraft(null); setEditor(null); save.reset(); setReviewed(false); }}>Отменить правки</Button><Button disabled={save.isPending || uploading || Boolean(editor) || items.some(item => !item.title.trim())} onClick={() => void persist()}>{save.isPending && <Loader2 className="size-4 animate-spin" />}Сохранить изменения</Button></div>}
      </>}
    </section>
    <Dialog open={Boolean(editor)} onOpenChange={open => { if (!open && !save.isPending && !uploading) setEditor(null); }}>
      <DialogContent data-product-shell data-max-studio className="max-catalog-dialog max-project-setup" overlayClassName="max-catalog-overlay">
        <DialogHeader className="max-catalog-dialog-header"><DialogTitle>{editor && editor.baseline.content.some(item => item.id === editor.id) ? "Изменить позицию" : "Новая позиция"}</DialogTitle><DialogDescription>Сохранение обновляет данные каталога без запуска ИИ.</DialogDescription></DialogHeader>
        {editor && selected && <><fieldset disabled={save.isPending} className="max-catalog-fields"><MaxContentItemFields key={editor.id} projectId={projectId} index={0} item={selected} onPatch={patchItem} onUploadingChange={setUploading} /></fieldset>
          {save.error && <p role="alert" className="max-catalog-error">{conflict ? "Каталог изменился. Черновик сохранён в форме. Закройте окно и перенесите правки к актуальной версии." : errorCopy(save.error)}</p>}
          <div className="max-catalog-dialog-footer">{!editor.baseline.base.some(item => item.id === editor.id) && <Button variant="ghost" disabled={save.isPending || uploading} onClick={cancelNewItem}>Отменить добавление</Button>}<Button variant="outline" disabled={save.isPending || uploading} onClick={() => setEditor(null)}>К списку</Button><Button disabled={!draft || !selected.title.trim() || items.some(item => !item.title.trim()) || save.isPending || uploading} onClick={() => void persist()}>{save.isPending && <Loader2 className="size-4 animate-spin" />}Сохранить позицию</Button></div></>}
      </DialogContent>
    </Dialog>
    {applyConfig && <MaxProjectDataApplyDialog config={applyConfig} catalogIntegration onClose={() => setApplyConfig(null)} onReturnFocus={() => connectTrigger.current?.focus()} />}
  </MaxSectionShell>;
}
