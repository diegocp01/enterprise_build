import { describe, expect, it } from 'vitest';
import { baselineSnapshot, cloneBaselineSlots, technicians } from './fixture';
import { getCoverageTotals } from './coverage';
import { evaluateEligibility } from './eligibility';

describe('deterministic coverage fixture', () => {
  it('contains exactly eight slots with the canonical 7/1 totals', () => {
    const slots = cloneBaselineSlots();
    expect(slots).toHaveLength(8);
    expect(getCoverageTotals(slots)).toEqual({ filled: 7, uncovered: 1, total: 8 });
    expect(slots.find((slot) => slot.id === 'm-n-safety')).toMatchObject({
      shiftId: 'morning', zoneId: 'north', role: 'Safety inspection',
      requiredCertifications: ['OSHA-30'], assignedTechnicianId: null,
    });
    slots[0].assignedTechnicianId = null;
    expect(JSON.stringify(cloneBaselineSlots())).toBe(baselineSnapshot);
  });

  it('uses one structured evaluator for every eligibility reason', () => {
    const slots = cloneBaselineSlots();
    const safety = slots.find((slot) => slot.id === 'm-n-safety')!;
    const tech = (id: string) => technicians.find((technician) => technician.id === id)!;
    expect(evaluateEligibility(tech('theo'), safety, slots)).toMatchObject({
      eligible: false,
      missingCertifications: ['OSHA-30'],
      unavailableForShift: false,
      conflictingSlot: { id: 'm-c-hvac' },
    });
    expect(evaluateEligibility(tech('jules'), safety, slots)).toMatchObject({
      eligible: false, missingCertifications: [], unavailableForShift: true, conflictingSlot: null,
    });
    expect(evaluateEligibility(tech('amara'), safety, slots)).toEqual({
      eligible: true, missingCertifications: [], unavailableForShift: false, conflictingSlot: null,
    });
  });
});
