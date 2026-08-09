import { describe, expect, it } from 'vitest';
import { workspaceStatusAfterApprovalError, workspaceStatusFor, workspaceStatusForCalendarWrite } from './commandAgentStore';

describe('workspaceStatusFor', () => {
  it('distinguishes clarification, confirmation, and Calendar permission waits', () => {
    expect(workspaceStatusFor('needs_goal_clarification')).toBe('waiting_clarification');
    expect(workspaceStatusFor('waiting_understanding_confirmation')).toBe('waiting_confirmation');
    expect(workspaceStatusFor('waiting_calendar_write_approval')).toBe('waiting_permission');
    expect(workspaceStatusFor('cancelled')).toBe('cancelled');
  });

  it('keeps blocked model status authoritative', () => {
    expect(workspaceStatusFor('waiting_final_review', 'blocked_model')).toBe('blocked_model');
  });
});

describe('workspaceStatusAfterApprovalError', () => {
  it('restores the completed status after a stale repeated approval', () => {
    expect(workspaceStatusAfterApprovalError('accepted')).toBe('accepted');
    expect(workspaceStatusAfterApprovalError('waiting_permission')).toBe('waiting_permission');
  });
});

describe('workspaceStatusForCalendarWrite', () => {
  it('marks a successful approval stream as completed', () => {
    expect(workspaceStatusForCalendarWrite(false)).toBe('accepted');
    expect(workspaceStatusForCalendarWrite(0)).toBe('accepted');
    expect(workspaceStatusForCalendarWrite(true)).toBe('failed');
    expect(workspaceStatusForCalendarWrite(1)).toBe('failed');
  });
});
