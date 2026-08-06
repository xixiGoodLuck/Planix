import { ShieldCheck } from 'lucide-react';
import type { CommandPermission } from '../../types';

interface PermissionPopoverProps {
  permission: CommandPermission;
  open: boolean;
  onToggle: () => void;
  onChange: (permission: CommandPermission) => void;
  t: (key: string) => string;
}

const options: CommandPermission[] = ['low', 'medium', 'high'];

function labelKey(permission: CommandPermission): string {
  return `command.permission${permission[0].toUpperCase()}${permission.slice(1)}`;
}

function descKey(permission: CommandPermission): string {
  return `command.permission${permission[0].toUpperCase()}${permission.slice(1)}Desc`;
}

export function PermissionPopover({ permission, open, onToggle, onChange, t }: PermissionPopoverProps) {
  return (
    <div className="permission-popover-wrap">
      <button
        className={`command-icon-button ${open ? 'active' : ''}`}
        onClick={onToggle}
        title={t('command.permission')}
        type="button"
      >
        <ShieldCheck size={18} />
      </button>
      {open && (
        <div className="permission-popover">
          <strong>{t('command.permission')}</strong>
          {options.map((option) => (
            <button
              key={option}
              className={permission === option ? 'active' : ''}
              onClick={() => onChange(option)}
              type="button"
            >
              <span>{t(labelKey(option))}</span>
              <small>{t(descKey(option))}</small>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
