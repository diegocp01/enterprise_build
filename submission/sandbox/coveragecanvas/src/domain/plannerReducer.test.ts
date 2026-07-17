import { describe, expect, it } from 'vitest';
import { baselineSnapshot } from './fixture';
import { getCoverageTotals } from './coverage';
import { createInitialState, plannerReducer } from './plannerReducer';

function select(state = createInitialState(), technicianId = 'amara') {
  const withSlot = plannerReducer(state, { type: 'select-slot', slotId: 'm-n-safety' });
  return plannerReducer(withSlot, { type: 'select-technician', technicianId });
}

describe('planner reducer', () => {
  it('rejects Theo with both causes without mutating assignments or history', () => {
    const state = select(createInitialState(), 'theo');
    const before = JSON.stringify(state.slots);
    const rejected = plannerReducer(state, { type: 'commit', slotId: 'm-n-safety', technicianId: 'theo' });
    expect(rejected.feedback?.message).toContain('missing OSHA-30');
    expect(rejected.feedback?.message).toContain('already assigned to HVAC call in Central during Morning');
    expect(JSON.stringify(rejected.slots)).toBe(before);
    expect(rejected.history).toEqual([]);
    expect(getCoverageTotals(rejected.slots)).toMatchObject({ filled: 7, uncovered: 1 });
  });

  it('rejects a Morning-unavailable technician without mutation', () => {
    const state = select(createInitialState(), 'jules');
    const rejected = plannerReducer(state, { type: 'commit', slotId: 'm-n-safety', technicianId: 'jules' });
    expect(rejected.feedback?.message).toContain('unavailable for Morning');
    expect(rejected.history).toEqual([]);
    expect(JSON.stringify(rejected.slots)).toBe(baselineSnapshot);
  });

  it('resets directly from a rejected attempt to a clean, focused baseline', () => {
    const selected = select(createInitialState(), 'theo');
    const rejected = plannerReducer(selected, { type: 'commit', slotId: 'm-n-safety', technicianId: 'theo' });
    expect(rejected.feedback?.kind).toBe('error');

    const reset = plannerReducer(rejected, { type: 'reset' });
    expect(JSON.stringify(reset.slots)).toBe(baselineSnapshot);
    expect(getCoverageTotals(reset.slots)).toEqual({ filled: 7, uncovered: 1, total: 8 });
    expect(reset.history).toEqual([]);
    expect(reset.feedback).toBeNull();
    expect(reset.selectedSlotId).toBeNull();
    expect(reset.selectedTechnicianId).toBeNull();
    expect(reset.focusIntent).toMatchObject({ target: 'slot', slotId: 'm-n-safety' });
  });

  it('commits only the selected slot and records success-only history', () => {
    const state = select();
    const committed = plannerReducer(state, { type: 'commit', slotId: 'm-n-safety', technicianId: 'amara' });
    expect(committed.slots.filter((slot, index) => slot.assignedTechnicianId !== state.slots[index].assignedTechnicianId).map((slot) => slot.id)).toEqual(['m-n-safety']);
    expect(getCoverageTotals(committed.slots)).toEqual({ filled: 8, uncovered: 0, total: 8 });
    expect(committed.history).toHaveLength(1);
    expect(committed.focusIntent?.target).toBe('undo');
  });

  it('rejects stale commits before mutation', () => {
    const state = select();
    const stale = plannerReducer(state, { type: 'commit', slotId: 'm-n-safety', technicianId: 'theo' });
    expect(stale.feedback?.message).toContain('selection changed');
    expect(stale.history).toEqual([]);
    expect(JSON.stringify(stale.slots)).toBe(baselineSnapshot);
  });

  it('rejects a filled-slot commit and preserves the latest valid undo entry', () => {
    const committed = plannerReducer(select(), { type: 'commit', slotId: 'm-n-safety', technicianId: 'amara' });
    const forcedFilledSelection = {
      ...committed,
      selectedSlotId: 'm-n-safety',
      selectedTechnicianId: 'jules',
    };
    const rejected = plannerReducer(forcedFilledSelection, {
      type: 'commit',
      slotId: 'm-n-safety',
      technicianId: 'jules',
    });
    expect(rejected.feedback?.message).toContain('already filled');
    expect(rejected.history).toEqual(committed.history);
    expect(rejected.slots).toEqual(committed.slots);
    expect(getCoverageTotals(rejected.slots)).toEqual({ filled: 8, uncovered: 0, total: 8 });
  });

  it('undoes the latest valid assignment and reset always clones the baseline', () => {
    const committed = plannerReducer(select(), { type: 'commit', slotId: 'm-n-safety', technicianId: 'amara' });
    const undone = plannerReducer(committed, { type: 'undo' });
    expect(JSON.stringify(undone.slots)).toBe(baselineSnapshot);
    expect(undone.history).toEqual([]);
    expect(undone.focusIntent).toMatchObject({ target: 'slot', slotId: 'm-n-safety' });
    const resetOnce = plannerReducer(committed, { type: 'reset' });
    const resetTwice = plannerReducer(resetOnce, { type: 'reset' });
    expect(JSON.stringify(resetOnce.slots)).toBe(baselineSnapshot);
    expect(JSON.stringify(resetTwice.slots)).toBe(baselineSnapshot);
    expect(resetTwice.feedback).toBeNull();
    expect(resetTwice.history).toEqual([]);
  });
});
