/**
 * `<DataTable>` — a pure, presentational table for cabinet lists. Generic over
 * the row type: hand it the rows you've already queried with Drizzle plus a
 * declarative `columns` spec and it renders a premium dark table (quiet
 * uppercase header, row hover, rounded card frame). No data fetching, no hooks
 * — server-component-safe. When `rows` is empty it renders `empty` (typically
 * an <EmptyState>) inside the same frame so a list never collapses to nothing.
 * Self-contained on Tailwind + the `--brand` token.
 */
import * as React from "react";

export interface Column<T> {
  /** Stable key, also used as the React key for the cell. */
  key: string;
  header: React.ReactNode;
  /** Cell renderer. Omit to render `String(row[key])` when `key` is a field. */
  render?: (row: T) => React.ReactNode;
  align?: "left" | "right" | "center";
  /** Tailwind width hint, e.g. "w-32" or "w-px whitespace-nowrap". */
  className?: string;
}

export interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  /** Unique key per row. Defaults to the array index (fine for static lists). */
  getRowKey?: (row: T, index: number) => string | number;
  /** Shown in place of the body when `rows` is empty (e.g. an <EmptyState>). */
  empty?: React.ReactNode;
  /** Optional caption rendered above the table (e.g. a count). */
  caption?: React.ReactNode;
}

const alignClass: Record<NonNullable<Column<unknown>["align"]>, string> = {
  left: "text-left",
  right: "text-right",
  center: "text-center",
};

export function DataTable<T extends Record<string, unknown>>({
  columns,
  rows,
  getRowKey,
  empty,
  caption,
}: DataTableProps<T>) {
  return (
    <div className="overflow-hidden rounded-2xl border border-white/10 bg-white/[0.02]">
      {caption ? (
        <div className="border-b border-white/5 px-5 py-3 text-sm text-zinc-400">
          {caption}
        </div>
      ) : null}

      {rows.length === 0 && empty ? (
        empty
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-white/8">
                {columns.map((col) => (
                  <th
                    key={col.key}
                    className={[
                      "whitespace-nowrap px-5 py-3 text-[0.7rem] font-semibold uppercase tracking-widest text-zinc-500",
                      alignClass[col.align ?? "left"],
                      col.className ?? "",
                    ].join(" ")}
                  >
                    {col.header}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr
                  key={getRowKey ? getRowKey(row, i) : i}
                  className="border-b border-white/5 transition-colors last:border-0 hover:bg-white/[0.03]"
                >
                  {columns.map((col) => (
                    <td
                      key={col.key}
                      className={[
                        "px-5 py-3.5 text-zinc-200",
                        alignClass[col.align ?? "left"],
                        col.className ?? "",
                      ].join(" ")}
                    >
                      {col.render
                        ? col.render(row)
                        : ((row[col.key] as React.ReactNode) ?? "—")}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
