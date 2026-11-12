"use client";

import { useState } from "react";
import { PAGE_SIZE, paginate, type PageSlice } from "@/lib/pagination";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

export function usePagedList<T>(items: readonly T[], filterKey: string, pageSize = PAGE_SIZE): PageSlice<T> & { setPage: (page: number) => void } {
  const [state, setState] = useState({ key: filterKey, page: 1 });
  let page = state.page;
  if (state.key !== filterKey) {
    page = 1;
    setState({ key: filterKey, page: 1 });
  }
  const view = paginate(items, page, pageSize);
  if (view.pages > 0 && page > view.pages) {
    page = view.pages;
    setState({ key: filterKey, page });
  }
  const shown = page === view.page ? view : paginate(items, page, pageSize);
  return {
    ...shown,
    setPage: (next) => setState({ key: filterKey, page: next }),
  };
}

export function PaginationBar({ page, pages, total, onPage, className }: { page: number; pages: number; total: number; onPage: (page: number) => void; className?: string }) {
  if (total <= 0) return null;
  return (
    <div className={cn("mt-3 flex flex-wrap items-center justify-between gap-2 text-sm", className)}>
      <span className="text-muted-foreground">共 {total} 条</span>
      <div className="flex items-center gap-2">
        <Button type="button" variant="outline" size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          上一页
        </Button>
        <span className="text-xs text-muted-foreground">
          第 {page} / {Math.max(pages, 1)} 页
        </span>
        <Button type="button" variant="outline" size="sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>
          下一页
        </Button>
      </div>
    </div>
  );
}
