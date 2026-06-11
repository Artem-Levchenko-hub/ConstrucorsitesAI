"use client";

import * as React from "react";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { type ListParams, type Row } from "@/lib/sdk";
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
import { EntityForm, type FieldSpec } from "./entity-form";
import { useEntity } from "./use-entity";

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
}

/** Plain display of a raw field value in the detail card — mirrors the table's
 *  default cell (—/Да/Нет/text) for columns without a custom `render`. */
function displayValue(value: unknown): React.ReactNode {
  if (value == null || value === "") return <span className="text-muted-foreground">—</span>;
  if (typeof value === "boolean") return value ? "Да" : "Нет";
  return String(value);
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
}: CrudResourceProps) {
  const allowBulk = selectable ?? canDelete;
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

  return (
    <div>
      {title ? (
        <PageHeader title={title} description={description} actions={createButton} />
      ) : createButton ? (
        <div className="mb-4 flex justify-end">{createButton}</div>
      ) : null}

      <DataTable
        columns={columns}
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
      />

      {/* Create / edit */}
      <Dialog open={formOpen} onOpenChange={setFormOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editing ? "Редактировать" : createLabel}</DialogTitle>
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
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{title ? `${title}: запись` : "Запись"}</DialogTitle>
          </DialogHeader>
          {viewing ? (
            <dl className="divide-y divide-border">
              {columns.map((col) => (
                <div
                  key={col.key}
                  className="grid grid-cols-1 gap-1 py-2.5 text-sm sm:grid-cols-[10rem_1fr] sm:gap-3"
                >
                  <dt className="text-muted-foreground">{col.header}</dt>
                  <dd className="font-medium break-words">
                    {col.render ? col.render(viewing) : displayValue(viewing[col.key])}
                  </dd>
                </div>
              ))}
            </dl>
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
