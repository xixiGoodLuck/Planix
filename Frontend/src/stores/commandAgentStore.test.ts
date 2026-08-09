import { describe, expect, it } from 'vitest';
import { workspaceStatusFor } from './commandAgentStore';

describe('workspaceStatusFor', () => {
  it('distinguishes clarification, confirmation, and Calendar permission waits', () => {
    expect(workspaceStatusFor('needs_goal_clarification')).toBe('waiting_clarification');
    expect(workspaceStatusFor('waiting_understanding_confirmation')).toBe('waiting_confirmation');
    expect(workspaceStatusFor('waiting_calendar_write_approval')).toBe('waiting_permission');
  });

  it('keeps blocked model status authoritative', () => {
    expect(workspaceStatusFor('waiting_final_review', 'blocked_model')).toBe('blocked_model');
  });
});
