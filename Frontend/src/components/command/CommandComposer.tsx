import { ArrowUp, MessageCircle, Plus } from 'lucide-react';
import { useLayoutEffect, useRef, useState } from 'react';
import type { CommandThreadMessage } from '../../stores/commandAgentStore';
import type { CommandMode, CommandPermission } from '../../types';
import { DeepPlanningActionBar } from './DeepPlanningActionBar';
import { PermissionPopover } from './PermissionPopover';
import { WorkbenchToggle } from './WorkbenchToggle';

interface CommandComposerProps {
  sending: boolean;
  disabled?: boolean;
  messages: CommandThreadMessage[];
  mode: CommandMode;
  permission: CommandPermission;
  onSend: (value: string) => boolean | void | Promise<boolean | void>;
  onChatToggle: () => void;
  onWorkbenchToggle: () => void;
  onPermissionChange: (permission: CommandPermission) => void;
  t: (key: string) => string;
}

export function CommandComposer(props: CommandComposerProps) {
  const {
    sending,
    disabled = false,
    messages,
    mode,
    permission,
    onSend,
    onChatToggle,
    onWorkbenchToggle,
    onPermissionChange,
    t
  } = props;
  const [value, setValue] = useState('');
  const [permissionOpen, setPermissionOpen] = useState(false);
  const [scrollable, setScrollable] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const minRows = 2;
  const maxRows = 5;

  useLayoutEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    const styles = window.getComputedStyle(textarea);
    const lineHeight = Number.parseFloat(styles.lineHeight) || 22;
    const paddingY = Number.parseFloat(styles.paddingTop) + Number.parseFloat(styles.paddingBottom);
    const minHeight = lineHeight * minRows + paddingY;
    const maxHeight = lineHeight * maxRows + paddingY;
    textarea.style.height = 'auto';
    const nextHeight = Math.min(Math.max(textarea.scrollHeight, minHeight), maxHeight);
    const shouldScroll = textarea.scrollHeight > maxHeight + 1;
    textarea.style.height = `${nextHeight}px`;
    textarea.style.overflowY = shouldScroll ? 'auto' : 'hidden';
    setScrollable(shouldScroll);
  }, [value]);

  function submit() {
    const trimmed = value.trim();
    if (!trimmed || sending || disabled) return;
    const accepted = onSend(trimmed);
    if (accepted !== false) setValue('');
  }

  return (
    <div className="command-composer-shell">
      <div className="command-composer">
        <div className="command-composer-actions">
          <button className="command-icon-button" type="button" title={t('command.attach')}>
            <Plus size={18} />
          </button>
          <button
            className={`command-icon-button chat-mode-toggle ${mode === 'chat' ? 'active' : ''}`}
            type="button"
            onClick={onChatToggle}
            title={mode === 'chat' ? t('command.chatModeOn') : t('command.chatModeOff')}
          >
            <MessageCircle size={18} />
          </button>
          <PermissionPopover
            permission={permission}
            open={permissionOpen}
            onToggle={() => setPermissionOpen((current) => !current)}
            onChange={(next) => {
              onPermissionChange(next);
              setPermissionOpen(false);
            }}
            t={t}
          />
          <WorkbenchToggle active={mode === 'workbench'} onToggle={onWorkbenchToggle} t={t} />
        </div>
        <textarea
          ref={textareaRef}
          className={scrollable ? 'scrollable' : ''}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          placeholder={t('command.placeholder')}
          disabled={disabled && !sending}
          rows={2}
        />
        <button
          className={`command-send ${value.trim() ? 'ready' : ''}`}
          type="button"
          onClick={submit}
          disabled={!value.trim() || sending || disabled}
          title={t('command.send')}
        >
          <ArrowUp size={18} />
        </button>
      </div>
      <DeepPlanningActionBar disabled={sending || disabled} messages={messages} onSend={onSend} t={t} />
    </div>
  );
}
