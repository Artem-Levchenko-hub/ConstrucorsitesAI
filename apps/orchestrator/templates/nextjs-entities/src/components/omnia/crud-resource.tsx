"use client";

import * as React from "react";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { type ListParams, type Row } from "@/lib/sdk";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { PageHeader } from "./page-header";
import { DataTable, type Column, type FilterTab } from "./data-table";
import { GalleryGrid, type GalleryItem, type MediaCardProps } from "./gallery-grid";
import { RecordDetail } from "./record-detail";
import { EntityForm, type FieldSpec } from "./entity-form";
import { useEntity } from "./use-entity";

/**
 * Row→card mapping for the gallery view. Each accessor pulls one field off a
 * record; only `title` is required. Keep `image` pointing at the record's
 * image/photo/cover field (the seeder fills those with ready-to-render tiles).
 */
export interface MediaMap {
  image?: (row: Row) => string | undefined;
  title: (row: Row) => React.ReactNode;
  subtitle?: (row: Row) => React.ReactNode;
  /** Prominent footer value, e.g. `formatRub(row.price)`. */
  price?: (row: Row) => React.ReactNode;
  /** Overlay pill on the image, e.g. a status or «Хит». */
  badge?: (row: Row) => React.ReactNode;
  /** Quiet footer-right value, e.g. a rating. */
  metaRight?: (row: Row) => React.ReactNode;
  aspect?: MediaCardProps["aspect"];
}

export interface CrudResourceProps {
  entity: string;
  title?: string;
  description?: string;
  columns: Column<Row>[];
  fields: FieldSpec[];
  listParams?: ListParams;
  searchable?: boolean;
  searchKeys?: string[];
  /** Row field the quick-filter segments match (e.g. "status"). */
  filterField?: string;
  /** Quick-filter segments above the table. First is usually `{ label: "Все", value: null }`. */
  filterTabs?: FilterTab[];
  pageSize?: number;
  /** Show a CSV export button on the table. On by default for managed screens. */
  exportable?: boolean;
  /**
   * Let the user hide/show columns via a "Колонки" menu. On by default; the menu
   * only appears once the table has enough columns (≥3) to be worth it.
   */
  columnToggle?: boolean;
  /**
   * Let the user tick rows for bulk actions (bulk delete + export selected).
   * Defaults to whatever `canDelete` is, so deletable lists get it for free.
   */
  selectable?: boolean;
  /**
   * Let the user switch between comfortable and compact row height. On by
   * default; the toggle only appears once the list has enough rows (≥10).
   */
  densityToggle?: boolean;
  /**
   * Open a read-only detail card with every field of a record when its row is
   * clicked (incl. columns hidden via the column menu). On by default; the edit
   * and delete buttons inside it reuse the same dialogs.
   */
  rowDetail?: boolean;
  canCreate?: boolean;
  canEdit?: boolean;
  canDelete?: boolean;
  createLabel?: string;
  /**
   * Render the collection as an image-forward card grid instead of a table —
   * for visual niches a gallery sells better than rows of text (каталог,
   * недвижимость, меню, портфолио, события). Requires `media`. Default "table".
   */
  view?: "table" | "gallery";
  /** Row→card mapping for `view="gallery"`. Ignored in table view. */
  media?: MediaMap;
}

/** Plain display of a raw field value in the detail card — mirrors the table's
 *  default cell (—/Да/Нет/text) for columns without a custom `render`. */
function displayValue(value: unknown): React.ReactNode {
  if (value == null || value === "") return <span className="text-muted-foreground">—</span>;
  if (typeof value === "boolean") return value ? "Да" : "Нет";
  return String(value);
}

/** Render one column's cell for the detail view — its custom `render` if any,
 *  else the same plain value the table would show. */
function renderCell(col: Column<Row>, row: Row): React.ReactNode {
  return col.render ? col.render(row) : displayValue(row[col.key]);
}

/**
 * A complete, managed CRUD screen for one entity: header + create button +
 * searchable/sortable/paginated table + create/edit dialog (schema-driven form)
 * + delete confirm — all wired to the SDK. The fast path for any list-of-things
 * screen (clients, deals, products…). Compose the kit by hand for dashboards or
 * bespoke views.
 *
 *   <CrudResource
 *     entity="Client"
 *     title="Клиенты"
 *     columns={[{ key: "name", header: "Имя", sortable: true }, ...]}
 *     fields={[{ name: "name", label: "Имя", kind: "text", required: true }, ...]}
 *   />
 */
export function CrudResource({
  entity,
  title,
  description,
  columns,
  fields,
  listParams,
  searchable = true,
  searchKeys,
  filterField,
  filterTabs,
  pageSize = 10,
  exportable = true,
  columnToggle = true,
  selectable,
  densityToggle = true,
  rowDetail = true,
  canCreate = true,
  canEdit = true,
  canDelete = true,
  createLabel = "Создать",
  view = "table",
  media,
}: CrudResourceProps) {
  const allowBulk = selectable ?? canDelete;
  // Make every column sortable by default so operators can order a managed list
  // without the writer remembering `sortable: true` per column. A column can opt
  // out with an explicit `sortable: false`. Spread keeps render/align/className.
  const tableColumns = React.useMemo(
    () => columns.map((c) => ({ ...c, sortable: c.sortable ?? true })),
    [columns],
  );
  // Auto-expand reference fields so columns can render the related row
  // (e.g. `row._expanded.clientId.name`) without the caller wiring `expand`.
  const expand = React.useMemo(
    () => fields.filter((f) => f.kind === "reference").map((f) => f.name),
    [fields],
  );
  const mergedParams = React.useMemo(
    () => ({
      ...listParams,
      expand: [...(listParams?.expand ?? []), ...expand],
    }),
    [listParams, expand],
  );
  const data = useEntity(entity, expand.length ? mergedParams : listParams);
  const [formOpen, setFormOpen] = React.useState(false);
  const [editing, setEditing] = React.useState<Row | null>(null);
  const [viewing, setViewing] = React.useState<Row | null>(null);
  const [deleting, setDeleting] = React.useState<Row | null>(null);
  const [bulkDeleting, setBulkDeleting] = React.useState<{
    rows: Row[];
    clear: () => void;
  } | null>(null);
  const [busy, setBusy] = React.useState(false);

  function openCreate() {
    setEditing(null);
    setFormOpen(true);
  }
  function openEdit(row: Row) {
    setEditing(row);
    setFormOpen(true);
  }

  // Deep-link: reaching the list with `?create=1` pops the create form straight
  // away, so an onboarding/checklist CTA can land the user on the new-record
  // form without a dedicated `/new` route (creation lives in the dialog, not a
  // page). Read from window — client-only, no Suspense boundary needed.
  React.useEffect(() => {
    if (!canCreate) return;
    if (new URLSearchParams(window.location.search).get("create") === "1") {
      setEditing(null);
      setFormOpen(true);
    }
  }, [canCreate]);

  async function handleSubmit(payload: Record<string, unknown>) {
    if (editing) {
      await data.update(editing.id, payload);
      toast.success("Изменения сохранены");
    } else {
      await data.create(payload);
      toast.success("Запись создана");
    }
    setFormOpen(false);
  }

  async function handleDelete() {
    if (!deleting) return;
    setBusy(true);
    try {
      await data.remove(deleting.id);
      toast.success("Удалено");
      setDeleting(null);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Не удалось удалить");
    } finally {
      setBusy(false);
    }
  }

  async function handleBulkDelete() {
    if (!bulkDeleting) return;
    const { rows, clear } = bulkDeleting;
    setBusy(true);
    // allSettled so one failed row doesn't abort the rest; report the tally.
    const results = await Promise.allSettled(rows.map((r) => data.remove(r.id)));
    const failed = results.filter((r) => r.status === "rejected").length;
    setBusy(false);
    setBulkDeleting(null);
    clear();
    if (failed === 0) {
      toast.success(`Удалено: ${rows.length}`);
    } else if (failed === rows.length) {
      toast.error("Не удалось удалить");
    } else {
      toast.error(`Удалено: ${rows.length - failed}, с ошибкой: ${failed}`);
    }
  }

  const createButton = canCreate ? (
    <Button onClick={openCreate}>
      <Plus />
      {createLabel}
    </Button>
  ) : null;

  const rowActions =
    canEdit || canDelete
      ? (row: Row) => (
          <>
            {canEdit ? (
              <Button variant="ghost" size="icon" onClick={() => openEdit(row)} aria-label="Изменить">
                <Pencil className="size-4" />
              </Button>
            ) : null}
            {canDelete ? (
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setDeleting(row)}
                aria-label="Удалить"
                className="text-muted-foreground hover:text-destructive"
              >
                <Trash2 className="size-4" />
              </Button>
            ) : null}
          </>
        )
      : undefined;

  const useGallery = view === "gallery" && !!media;

  // Build the card models once per row set. Search keywords fold the searchable
  // columns' raw text so the gallery's search box matches the same fields the
  // table would, even when a column renders a custom (non-string) node.
  const keywordKeys = searchKeys ?? columns.map((c) => c.key);
  const galleryItems: GalleryItem[] = React.useMemo(() => {
    if (!useGallery || !media) return [];
    return data.rows.map((row) => ({
      id: row.id,
      image: media.image?.(row),
      title: media.title(row),
      subtitle: media.subtitle?.(row),
      price: media.price?.(row),
      badge: media.badge?.(row),
      metaRight: media.metaRight?.(row),
      aspect: media.aspect,
      onClick: rowDetail ? () => setViewing(row) : undefined,
      actions: rowActions ? rowActions(row) : undefined,
      keywords: keywordKeys
        .map((k) => {
          const v = (row as Record<string, unknown>)[k];
          return v == null ? "" : String(v);
        })
        .join(" "),
    }));
  }, [useGallery, media, data.rows, rowDetail, rowActions, keywordKeys]);

  return (
    <div>
      {title ? (
        <PageHeader title={title} description={description} actions={createButton} />
      ) : createButton ? (
        <div className="mb-4 flex justify-end">{createButton}</div>
      ) : null}

      {useGallery ? (
        <GalleryGrid
          items={galleryItems}
          loading={data.loading}
          searchable={searchable}
          pageSize={pageSize}
          emptyAction={
            canCreate ? (
              <Button onClick={openCreate}>
                <Plus />
                {createLabel}
              </Button>
            ) : undefined
          }
        />
      ) : (
      <DataTable
        columns={tableColumns}
        rows={data.rows}
        loading={data.loading}
        searchable={searchable}
        searchKeys={searchKeys}
        filterField={filterField}
        filterTabs={filterTabs}
        pageSize={pageSize}
        exportable={exportable}
        exportFilename={`${title ?? entity}.csv`}
        columnToggle={columnToggle}
        selectable={allowBulk}
        densityToggle={densityToggle}
        bulkActions={
          canDelete
            ? (selected, clear) => (
                <Button
                  type="button"
                  variant="destructive"
                  size="sm"
                  onClick={() => setBulkDeleting({ rows: selected, clear })}
                >
                  <Trash2 className="size-4" />
                  Удалить ({selected.length})
                </Button>
              )
            : undefined
        }
        rowActions={rowActions}
        onRowClick={rowDetail ? (row) => setViewing(row) : undefined}
        emptyAction={
          canCreate ? (
            <Button onClick={openCreate}>
              <Plus />
              {createLabel}
            </Button>
          ) : undefined
        }
      />
      )}

      {/* Create / edit */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent className={cn(fields.length > 3 && "sm:max-w-2xl")}>
          <DialogHeader>
            <DialogTitle>{editing ? "Редактировать" : createLabel}</DialogTitle>
            <DialogDescription>
              {editing
                ? "Измените поля и сохраните изменения."
                : "Заполните поля ниже, чтобы создать запись."}
            </DialogDescription>
          </DialogHeader>
          <EntityForm
            key={editing?.id ?? "new"}
            fields={fields}
            initial={editing ?? undefined}
            onSubmit={handleSubmit}
            onCancel={() => setFormOpen(false)}
          />
        </DialogContent>
      </Dialog>

      {/* Row detail (read view) */}
      <Dialog open={!!viewing} onOpenChange={(o) => !o && setViewing(null)}>
        <DialogContent
          className={cn(
            "max-h-[85vh] overflow-y-auto",
            media ? "sm:max-w-3xl" : "sm:max-w-lg",
          )}
        >
          <DialogHeader className="sr-only">
            {/* Required for a11y; the visible heading lives in <RecordDetail>. */}
            <DialogTitle>{title ? `${title}: запись` : "Запись"}</DialogTitle>
          </DialogHeader>
          {viewing ? (
            <RecordDetail
              title={
                media
                  ? media.title(viewing)
                  : columns[0]
                    ? renderCell(columns[0], viewing)
                    : (title ?? "Запись")
              }
              eyebrow={media?.subtitle?.(viewing)}
              image={media?.image?.(viewing)}
              badge={media?.badge?.(viewing)}
              price={media?.price?.(viewing)}
              metaRight={media?.metaRight?.(viewing)}
              aspect={media?.aspect}
              fields={columns.slice(1).map((col) => ({
                label: col.header,
                value: renderCell(col, viewing),
              }))}
            />
          ) : null}
          {canEdit || canDelete ? (
            <DialogFooter>
              {canEdit ? (
                <Button
                  variant="outline"
                  onClick={() => {
                    const row = viewing;
                    setViewing(null);
                    if (row) openEdit(row);
                  }}
                >
                  Изменить
                </Button>
              ) : null}
              {canDelete ? (
                <Button
                  variant="destructive"
                  onClick={() => {
                    const row = viewing;
                    setViewing(null);
                    if (row) setDeleting(row);
                  }}
                >
                  Удалить
                </Button>
              ) : null}
            </DialogFooter>
          ) : null}
        </DialogContent>
      </Dialog>

      {/* Delete confirm */}
      <Dialog open={!!deleting} onOpenChange={(o) => !o && setDeleting(null)}>
        <DialogContent showCloseButton={false} className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Удалить запись?</DialogTitle>
            <DialogDescription>
              Действие необратимо. Запись будет удалена безвозвратно.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleting(null)} disabled={busy}>
              Отмена
            </Button>
            <Button variant="destructive" onClick={handleDelete} disabled={busy}>
              Удалить
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Bulk delete confirm */}
      <Dialog open={!!bulkDeleting} onOpenChange={(o) => !o && !busy && setBulkDeleting(null)}>
        <DialogContent showCloseButton={false} className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Удалить выбранные записи?</DialogTitle>
            <DialogDescription>
              {bulkDeleting
                ? `Будет удалено записей: ${bulkDeleting.rows.length}. Действие необратимо.`
                : null}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setBulkDeleting(null)} disabled={busy}>
              Отмена
            </Button>
            <Button variant="destructive" onClick={handleBulkDelete} disabled={busy}>
              Удалить
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
