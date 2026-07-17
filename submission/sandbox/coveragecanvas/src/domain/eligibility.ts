import { getShift, getZone } from './fixture';
import type { EligibilityResult, RoleSlot, Technician } from './types';

export function evaluateEligibility(
  technician: Technician,
  slot: RoleSlot,
  slots: readonly RoleSlot[],
): EligibilityResult {
  const missingCertifications = slot.requiredCertifications.filter(
    (certification) => !technician.certifications.includes(certification),
  );
  const unavailableForShift = !technician.availableShiftIds.includes(slot.shiftId);
  const conflictingSlot = slots.find(
    (candidate) =>
      candidate.id !== slot.id &&
      candidate.shiftId === slot.shiftId &&
      candidate.assignedTechnicianId === technician.id,
  ) ?? null;

  return {
    eligible: missingCertifications.length === 0 && !unavailableForShift && conflictingSlot === null,
    missingCertifications,
    unavailableForShift,
    conflictingSlot,
  };
}

export function eligibilitySummary(result: EligibilityResult, slot: RoleSlot): string {
  if (result.eligible) {
    return `Eligible — available for ${getShift(slot.shiftId).name}, certified, and not booked in this shift.`;
  }

  const reasons: string[] = [];
  if (result.missingCertifications.length > 0) {
    reasons.push(`missing ${result.missingCertifications.join(', ')}`);
  }
  if (result.unavailableForShift) {
    reasons.push(`unavailable for ${getShift(slot.shiftId).name}`);
  }
  if (result.conflictingSlot) {
    reasons.push(
      `already assigned to ${result.conflictingSlot.role} in ${getZone(result.conflictingSlot.zoneId).name} during ${getShift(result.conflictingSlot.shiftId).name}`,
    );
  }
  return `Blocked — ${reasons.join('; ')}.`;
}

export function rejectionMessage(technician: Technician, slot: RoleSlot, result: EligibilityResult): string {
  const details = eligibilitySummary(result, slot).replace(/^Blocked — /, '').replace(/\.$/, '');
  return `Assignment not made. ${technician.name} is ${details}. ${slot.role} remains uncovered.`;
}
