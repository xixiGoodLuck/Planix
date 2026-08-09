export function handlePlanAddClick(onAdd: () => void): void {
  onAdd();
}

export function handlePlanInputKeyDown(key: string, onAdd: () => void): void {
  if (key === 'Enter') handlePlanAddClick(onAdd);
}
