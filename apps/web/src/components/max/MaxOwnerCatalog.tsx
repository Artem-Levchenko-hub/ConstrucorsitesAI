"use client";

import { useEffect, useRef, useState, type ComponentProps } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, EyeOff, ImagePlus, Loader2, PackageOpen, Pencil, Plus, RefreshCw, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ApiError } from "@/lib/api/client";
import { getMaxProjectConfig, getMaxProjectContent, saveMaxProjectConfig, saveMaxProjectContent } from "@/lib/api/max-studio";
import type { MaxContentDelivery, MaxContentItem, MaxProjectConfig, MaxProjectContent } from "@/lib/api/types";
import { catalogDraft, newMaxContentItem, rebaseCatalogDraft, type MaxCatalogDraft } from "@/lib/max-catalog";
import { MaxContentItemFields } from "./MaxContentItemEditor";
import { MaxProjectDataApplyDialog } from "./MaxProjectDataApplyDialog";
import { MaxSectionShell } from "./MaxSectionShell";
import { useMaxWorkspaceDraftGuard } from "./use-max-workspace-draft-guard";
import "./max-project-setup.css";
import "./max-owner-catalog.css";

const availability = { in_stock: "В наличии", on_request: "Под заказ", out_of_stock: "Нет в наличии" };
type CatalogSave = MaxCatalogDraft & { reapply?: boolean };
const SOURCE_DELIVERY = "source_delivery_unverified";
const deliveryCopy: Record<MaxContentDelivery["state"], string> = {
  applied: "Обновлено", pending: "Обновляется", unknown: "Обновление не подтверждено",
  not_published: "Ещё не опубликовано", blocked: "Обновление приостановлено", deferred: "Обновится после пробуждения", not_created: "Ещё не создан",
};
function errorCopy(error: unknown) { return error instanceof ApiError ? error.message : "Не удалось выполнить запрос. Повторите попытку."; }
function DeliveryStatus({ label, status }: { label: string; status: MaxContentDelivery }) {
  return <div className="max-catalog-delivery" data-state={status.state}><span>{label}</span><strong>{deliveryCopy[status.state]}</strong></div>;
}

function CatalogShell({ embedded, children, ...props }: ComponentProps<typeof MaxSectionShell> & { embedded?: boolean }) {
  return embedded ? <>{children}</> : <MaxSectionShell {...props}>{children}</MaxSectionShell>;
}

export function MaxOwnerCatalog({ projectId, projectName, embedded = false, onDirtyChange }: { projectId: string; projectName: string; embedded?: boolean; onDirtyChange?: (dirty: boolean) => void }) {
  const qc = useQueryClient();
  const contentQuery = { queryKey: ["max-content", projectId], queryFn: () => getMaxProjectContent(projectId), retry: false };
  const [draft, setDraft] = useState<MaxCatalogDraft | null>(null);
  const [editor, setEditor] = useState<{ id: string; initial: MaxContentItem; baseline: MaxCatalogDraft; view: "details" | "photo" } | null>(null);
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
  const editorDialog = useRef<HTMLDivElement>(null);
  const editorView = editor?.view;
  useEffect(() => {
    if (editorView === "details") editorDialog.current?.querySelector<HTMLInputElement>('input[aria-label="Название элемента 1"]')?.focus();
  }, [editorView]);
  const save = useMutation({
    onMutate: () => qc.cancelQueries({ queryKey: contentQuery.queryKey }),
    mutationFn: async (value: CatalogSave): Promise<MaxProjectContent> => {
      const config = await getMaxProjectConfig(projectId);
      if (config.application_mode !== "source") return saveMaxProjectContent(projectId, value.version, value.content);
      // The legacy PUT updates managed files and the source snapshot. A content
      // PATCH would persist the rows but miss that application path.
      // Keep the draft version: the server compares the normalized payload and
      // can confirm an identical save after a lost response without a new HEAD.
      let saved: MaxProjectConfig;
      try {
        saved = await saveMaxProjectConfig(projectId, { ...config.config, content: value.content }, value.version);
      } catch (error) {
        // The API distinguishes a new project from a dangling snapshot with
        // this message. The no-snapshot rejection happens before any write.
        if (!(error instanceof ApiError) || error.status !== 409 || error.code !== "project_empty" || error.message !== "У проекта нет исходного снимка") {
          throw error;
        }
        return saveMaxProjectContent(projectId, value.version, value.content);
      }
      const delivery = {
        state: "unknown" as const, reason: SOURCE_DELIVERY, desired_config_version: saved.config_version,
        controller_config_version: null, confirmed_config_version: null, checked_at: null,
      };
      return {
        project_id: saved.project_id, config_version: saved.config_version, content: saved.config.content,
        saved: { state: "saved", config_version: saved.config_version }, public: delivery, preview: delivery,
      };
    },
    onSuccess: async (data, value) => {
      // Also cancel a read queued after onMutate: it must not replace this receipt.
      await qc.cancelQueries({ queryKey: contentQuery.queryKey });
      qc.setQueryData(contentQuery.queryKey, data);
      if (data.public.reason === SOURCE_DELIVERY) {
        // The preview history refetches versions once the refreshed HEAD moves.
        void qc.invalidateQueries({ queryKey: ["project-versions", projectId], refetchType: "none" });
        void qc.invalidateQueries({ queryKey: ["snapshots", projectId] });
        void qc.invalidateQueries({ queryKey: ["max-preview-session", projectId] });
        void qc.invalidateQueries({ queryKey: ["max-readiness", projectId] });
        // Delivery status can fail independently of the confirmed source write.
        void qc.fetchQuery({ ...contentQuery, staleTime: 0 }).catch(() => undefined);
      }
      void qc.invalidateQueries({ queryKey: ["max-config", projectId] });
      if (!value.reapply) { setDraft(null); setEditor(null); setReviewed(false); }
    },
  });
  const dirty = Boolean(draft) || Boolean(editor);
  const savingChanges = uploading || save.isPending || connecting;
  useMaxWorkspaceDraftGuard(!embedded && dirty, !embedded && savingChanges);
  useEffect(() => { onDirtyChange?.(dirty || savingChanges); return () => onDirtyChange?.(false); }, [onDirtyChange, dirty, savingChanges]);
  const content = useQuery({
    ...contentQuery,
    enabled: !save.isPending,
    refetchInterval: query => query.state.data && [query.state.data.public, query.state.data.preview].some(status => status.reason !== SOURCE_DELIVERY && (status.state === "pending" || status.state === "unknown")) ? 5_000 : false,
  });
  const items = draft?.content ?? content.data?.content ?? [];
  // The displayed form and its baseline must describe the same snapshot.
  const selected = editor ? draft?.content.find(item => item.id === editor.id) ?? editor.initial : null;
  const saveError = save.error instanceof ApiError ? save.error : null;
  const conflict = saveError?.status === 409 && saveError.code === "conflict" && saveError.details?.reason === "config_version_conflict";
  const busy = saveError?.status === 409 && (saveError.code === "generation_active"
    || (saveError.code === "operation_conflict" && saveError.details?.reason === "publication_active"));
  const saveErrorCopy = busy && draft
    ? `${errorCopy(save.error)}. Черновик сохранён в форме. После завершения повторите сохранение в этом окне.`
    : errorCopy(save.error);
  const sourceSaved = save.data?.public.reason === SOURCE_DELIVERY && save.data.config_version === content.data?.config_version;
  const deliveryPending = !sourceSaved && content.data && [content.data.public.state, content.data.preview.state].some(state => state === "pending" || state === "unknown");
  const needsCatalogConnection = [content.data?.public.reason, content.data?.preview.reason].includes("catalog_integration_required");
  const categories = [...new Set(items.map(item => item.category).filter(Boolean))].sort();
  if (category && !categories.includes(category)) setCategory("");
  const filtered = items.filter(item => (visibility === "all" || item.active === (visibility === "visible"))
    && (!category || item.category === category)
    && `${item.title} ${item.description} ${item.category}`.toLocaleLowerCase("ru").includes(query.toLocaleLowerCase("ru")));

  function editItem(item: MaxContentItem, view: "details" | "photo" = "details") {
    if (!content.data) return;
    setEditor({ id: item.id, initial: item, baseline: draft ?? catalogDraft(content.data), view });
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

  return <CatalogShell embedded={embedded} projectId={projectId} projectName={projectName} active="catalog" eyebrow="Каталог" title="Товары и услуги" lead="Меняйте позиции, цены и наличие. Обычное сохранение не расходует баланс ИИ.">
    <section className="max-owner-catalog" aria-label="Каталог приложения">
      {sourceSaved && <p className="max-catalog-notice" role="status">Позиции сохранены в исходниках. Проверьте предпросмотр. Чтобы показать изменения посетителям, при необходимости опубликуйте новую версию.</p>}
      {content.data && !sourceSaved && <div className="max-catalog-status" role="status">
        <div className="max-catalog-delivery" data-state="saved"><span>Данные в Yleum</span><strong>{draft ? "Есть несохранённые правки" : "Сохранены"}</strong></div>
        <DeliveryStatus label="Публичное приложение" status={content.data.public} />
        <DeliveryStatus label="Предпросмотр" status={content.data.preview} />
      </div>}
      {deliveryPending && <div className="max-catalog-notice"><p>Данные сохранены в Yleum. Обновление приложения ещё не подтверждено.</p><Button variant="outline" disabled={Boolean(draft) || Boolean(editor) || uploading || save.isPending || connecting || Boolean(applyConfig)} onClick={() => void reapplySaved()}>{save.isPending && save.variables?.reapply ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}Повторить применение</Button></div>}
      {needsCatalogConnection && <div className="max-catalog-notice">
        <div><strong>Подключите каталог к приложению один раз</strong><p>Позиции уже можно редактировать. Чтобы они появились на экранах, нужна доработка ИИ и публикация. Доработка расходует баланс.</p></div>
        <Button ref={connectTrigger} variant="outline" disabled={connecting || Boolean(draft) || save.isPending} onClick={() => void connectCatalog()}>{connecting && <Loader2 className="size-4 animate-spin" />}Подключить каталог</Button>
      </div>}
      {connectError && <p role="alert" className="max-catalog-error">{connectError}</p>}
      {content.isError && <div role="alert" className="max-catalog-notice"><p>{content.data ? "Не удалось проверить актуальный каталог. Показаны последние полученные данные." : "Не удалось загрузить каталог."}</p><Button variant="outline" onClick={() => void content.refetch()}>Повторить</Button></div>}
      {content.isPending ? <p className="max-catalog-empty" role="status"><Loader2 className="size-5 animate-spin" />Загружаем каталог…</p> : content.data && <>
        <p className="max-catalog-count">{items.length} из 100 позиций, включая скрытые</p>
        <div className="max-catalog-toolbar">
          <label className="max-catalog-search"><Search className="size-4" aria-hidden="true" /><Input aria-label="Поиск по каталогу" placeholder="Найти товар или услугу" value={query} onChange={event => setQuery(event.target.value)} /></label>
          <select aria-label="Видимость позиций" value={visibility} onChange={event => setVisibility(event.target.value)}><option value="all">Все позиции</option><option value="visible">Видимые</option><option value="hidden">Скрытые</option></select>
          {categories.length > 0 && <select aria-label="Раздел каталога" value={category} onChange={event => setCategory(event.target.value)}><option value="">Все разделы</option>{categories.map(value => <option key={value}>{value}</option>)}</select>}
          <Button disabled={save.isPending || items.length >= 100} onClick={() => { const item = newMaxContentItem(); editItem(item); }}><Plus className="size-4" />Добавить позицию</Button>
        </div>
        {reviewed && <p className="max-catalog-notice" role="status">Актуальные данные загружены, ваши правки перенесены поверх них. Если одно поле меняли одновременно, оставлен ваш вариант. Проверьте карточки перед сохранением.</p>}
        {save.error && <div className="max-catalog-notice max-catalog-error" role="alert"><div><strong>{conflict ? "Каталог изменился в другой вкладке" : save.variables?.reapply ? "Не удалось повторить применение" : "Не удалось сохранить"}</strong><p>{conflict ? draft ? "Ваш черновик остался в форме. Загрузите актуальные данные и перенесите свои правки, затем проверьте карточки." : "Данные в Yleum сохранены. Обновите каталог перед повторным применением." : saveErrorCopy}</p></div>{conflict && <Button variant="outline" disabled={content.isFetching || uploading || save.isPending} onClick={() => void reviewLatest()}><RefreshCw className="size-4" />{draft ? "Перенести мои правки" : "Обновить каталог"}</Button>}</div>}
        {filtered.length ? <div className="max-catalog-grid">{filtered.map(item => <article className="max-catalog-card" key={item.id} data-hidden={!item.active}>
          <button type="button" className="max-catalog-photo-button" disabled={save.isPending}
            aria-label={`${item.image_url ? "Изменить" : "Добавить"} фото: ${item.title || "Новая позиция"}`} aria-haspopup="dialog" onClick={() => editItem(item, "photo")}>
            {item.image_url ? <>
              {/* Owner uploads have arbitrary public hosts; keep the native image loader. */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={item.image_url} alt="" loading="lazy" className="max-catalog-photo" />
              <span className="max-catalog-photo-edit"><Pencil aria-hidden="true" />Изменить фото</span>
            </> : <span className="max-catalog-photo max-catalog-photo-empty"><ImagePlus aria-hidden="true" /><span>Добавить фото<small>Файл или ссылка</small></span></span>}
          </button>
          <div className="max-catalog-card-body"><div className="max-catalog-meta"><span>{item.category || "Без раздела"}</span><span>{item.active ? availability[item.availability] : "Скрыто"}</span></div><h2>{item.title || "Новая позиция"}</h2>{item.description && <p>{item.description}</p>}<strong className="max-catalog-price">{item.price || "Цена не указана"}</strong>
          <div className="max-catalog-card-actions"><Button variant="outline" disabled={save.isPending} onClick={() => editItem(item)}><Pencil className="size-4" />Изменить</Button><Button variant="ghost" disabled={save.isPending} aria-label={`${item.active ? "Скрыть" : "Показать"} ${item.title}`} onClick={() => toggleVisibility(item)}>{item.active ? <EyeOff className="size-4" /> : <Eye className="size-4" />}{item.active ? "Скрыть" : "Показать"}</Button></div></div>
        </article>)}</div> : <div className="max-catalog-empty"><PackageOpen aria-hidden="true" /><h2>{items.length ? "Ничего не найдено" : "Добавьте первый товар или услугу"}</h2><p>{items.length ? "Попробуйте другой запрос или фильтр." : "Название, фото, цена и наличие — всё в одной карточке."}</p></div>}
        {draft && <div className="max-catalog-savebar"><p>Правки ещё не сохранены</p><Button variant="outline" disabled={save.isPending || uploading} onClick={() => { setDraft(null); setEditor(null); save.reset(); setReviewed(false); }}>Отменить правки</Button><Button disabled={save.isPending || uploading || Boolean(editor) || items.some(item => !item.title.trim())} onClick={() => void persist()}>{save.isPending && <Loader2 className="size-4 animate-spin" />}Сохранить изменения</Button></div>}
      </>}
    </section>
    <Dialog open={Boolean(editor)} onOpenChange={open => { if (!open && !save.isPending && !uploading) setEditor(null); }}>
      <DialogContent ref={editorDialog} data-product-shell data-max-studio className={`max-catalog-dialog max-project-setup${editorView === "photo" ? " max-catalog-photo-dialog" : ""}`} overlayClassName="max-catalog-overlay">
        <DialogHeader className="max-catalog-dialog-header"><DialogTitle>{editorView === "photo" ? `Фото позиции${selected?.title ? ` «${selected.title}»` : ""}` : editor && editor.baseline.content.some(item => item.id === editor.id) ? "Изменить позицию" : "Новая позиция"}</DialogTitle><DialogDescription>{editorView === "photo" ? "Загрузите файл или укажите ссылку на изображение. Фото применится после сохранения позиции." : "Сохранение обновляет данные каталога без запуска ИИ."}</DialogDescription></DialogHeader>
        {editor && selected && <><fieldset disabled={save.isPending} className="max-catalog-fields"><MaxContentItemFields key={editor.id} projectId={projectId} index={0} item={selected} onPatch={patchItem} onUploadingChange={setUploading} imageOnly={editorView === "photo"} /></fieldset>
          {save.error && <p role="alert" className="max-catalog-error">{conflict ? "Каталог изменился. Черновик сохранён в форме. Закройте окно и перенесите правки к актуальной версии." : saveErrorCopy}</p>}
          <div className="max-catalog-dialog-footer">{editorView === "photo" && <Button className="max-catalog-all-fields" variant="ghost" disabled={save.isPending || uploading} onClick={() => setEditor(current => current ? { ...current, view: "details" } : null)}>Все поля позиции</Button>}{!editor.baseline.base.some(item => item.id === editor.id) && <Button variant="ghost" disabled={save.isPending || uploading} onClick={cancelNewItem}>Отменить добавление</Button>}<Button variant="outline" disabled={save.isPending || uploading} onClick={() => setEditor(null)}>К списку</Button><Button disabled={!draft || !selected.title.trim() || items.some(item => !item.title.trim()) || save.isPending || uploading} onClick={() => void persist()}>{save.isPending && <Loader2 className="size-4 animate-spin" />}Сохранить позицию</Button></div></>}
      </DialogContent>
    </Dialog>
    {applyConfig && <MaxProjectDataApplyDialog config={applyConfig} catalogIntegration onClose={() => setApplyConfig(null)} onReturnFocus={() => connectTrigger.current?.focus()} />}
  </CatalogShell>;
}
