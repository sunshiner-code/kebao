export const PAGE_SIZE = 20;

export type PageSlice<T> = {
  items: T[];
  total: number;
  pages: number;
  page: number;
};

export function paginate<T>(items: readonly T[], page: number, pageSize = PAGE_SIZE): PageSlice<T> {
  const total = items.length;
  const pages = total === 0 ? 0 : Math.ceil(total / pageSize);
  const current = pages === 0 ? 1 : Math.min(Math.max(1, page), pages);
  const start = (current - 1) * pageSize;
  return {
    items: items.slice(start, start + pageSize),
    total,
    pages,
    page: current,
  };
}
