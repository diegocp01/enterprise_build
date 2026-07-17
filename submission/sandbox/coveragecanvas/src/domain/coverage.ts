import type { RoleSlot } from './types';

export interface CoverageTotals {
  filled: number;
  uncovered: number;
  total: number;
}

export function getCoverageTotals(slots: readonly RoleSlot[]): CoverageTotals {
  const filled = slots.filter((slot) => slot.assignedTechnicianId !== null).length;
  return { filled, uncovered: slots.length - filled, total: slots.length };
}
