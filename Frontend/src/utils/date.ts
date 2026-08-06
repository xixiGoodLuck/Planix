export function toISO(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

export function todayISO(): string {
  return toISO(new Date());
}

export function addMonths(date: Date, months: number): Date {
  const next = new Date(date);
  next.setMonth(next.getMonth() + months);
  return next;
}

export function monthKey(date: Date): string {
  return `${date.getFullYear()}_${date.getMonth() + 1}`;
}

export function getMonthDays(viewDate: Date): Array<{ iso: string; day: number; muted: boolean }> {
  const year = viewDate.getFullYear();
  const month = viewDate.getMonth();
  const first = new Date(year, month, 1);
  const startOffset = (first.getDay() + 6) % 7;
  const start = new Date(year, month, 1 - startOffset);
  return Array.from({ length: 42 }, (_, idx) => {
    const d = new Date(start);
    d.setDate(start.getDate() + idx);
    return { iso: toISO(d), day: d.getDate(), muted: d.getMonth() !== month };
  });
}

export function formatReadable(iso: string, lang: 'zh-CN' | 'en-US'): string {
  const date = new Date(`${iso}T00:00:00`);
  return new Intl.DateTimeFormat(lang, {
    month: 'long',
    day: 'numeric',
    weekday: 'short'
  }).format(date);
}
