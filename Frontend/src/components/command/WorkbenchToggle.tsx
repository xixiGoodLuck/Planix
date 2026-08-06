import { LayoutPanelTop } from 'lucide-react';

interface WorkbenchToggleProps {
  active: boolean;
  onToggle: () => void;
  t: (key: string) => string;
}

export function WorkbenchToggle({ active, onToggle, t }: WorkbenchToggleProps) {
  return (
    <button
      className={`command-icon-button workbench-toggle ${active ? 'active' : ''}`}
      onClick={onToggle}
      title={active ? t('command.workbenchOn') : t('command.workbenchOff')}
      type="button"
    >
      <LayoutPanelTop size={18} />
    </button>
  );
}
