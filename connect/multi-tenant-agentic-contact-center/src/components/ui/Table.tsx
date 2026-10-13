import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface Column<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  align?: "left" | "right" | "center";
  className?: string;
  headerClassName?: string;
}

export function DataTable<T>({
  columns,
  rows,
  keyFn,
  onRowClick,
  empty = "No records found.",
}: {
  columns: Column<T>[];
  rows: T[];
  keyFn: (row: T) => string;
  onRowClick?: (row: T) => void;
  empty?: ReactNode;
}) {
  const alignClass = (a?: string) =>
    a === "right" ? "text-right" : a === "center" ? "text-center" : "text-left";

  return (
    <div className="overflow-x-auto scrollbar-thin">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-ink-100">
            {columns.map((c) => (
              <th
                key={c.key}
                className={cn(
                  "whitespace-nowrap px-5 py-3 text-xs font-semibold uppercase tracking-wide text-ink-400",
                  alignClass(c.align),
                  c.headerClassName
                )}
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td
                colSpan={columns.length}
                className="px-5 py-12 text-center text-sm text-ink-400"
              >
                {empty}
              </td>
            </tr>
          ) : (
            rows.map((row) => (
              <tr
                key={keyFn(row)}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                className={cn(
                  "border-b border-ink-50 transition-colors last:border-0",
                  onRowClick && "cursor-pointer hover:bg-ink-50"
                )}
              >
                {columns.map((c) => (
                  <td
                    key={c.key}
                    className={cn(
                      "whitespace-nowrap px-5 py-3.5 text-ink-700",
                      alignClass(c.align),
                      c.className
                    )}
                  >
                    {c.render(row)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
