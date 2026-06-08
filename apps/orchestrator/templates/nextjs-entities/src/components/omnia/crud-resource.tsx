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
import { DataTable, type Column } from "./data-table";
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
  pageSize?: number;
  canCreate?: boolean;
  canEdit?: boolean;
  canDelete?: boolean;
  createLabel?: string;
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
  pageSize = 10,
  canCreate = true,
  canEdit = true,
  canDelete = true,
  createLabel = "Создать",
}: CrudResourceProps) {
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
  const [deleting, setDeleting] = React.useState<Row | null>(null);
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
        pageSize={pageSize}
        rowActions={rowActions}
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
    </div>
  );
}
