import type { RoleSlot, Shift, Technician, Zone } from './types';

export const shifts: readonly Shift[] = Object.freeze([
  { id: 'morning', name: 'Morning', hours: '6:00 AM–12:00 PM' },
  { id: 'midday', name: 'Midday', hours: '12:00 PM–6:00 PM' },
  { id: 'evening', name: 'Evening', hours: '6:00 PM–11:00 PM' },
]);

export const zones: readonly Zone[] = Object.freeze([
  { id: 'north', name: 'North' },
  { id: 'central', name: 'Central' },
  { id: 'south', name: 'South' },
]);

export const technicians: readonly Technician[] = Object.freeze([
  { id: 'priya', name: 'Priya Shah', availableShiftIds: ['morning', 'evening'], certifications: ['Electrical L2'] },
  { id: 'theo', name: 'Theo Brooks', availableShiftIds: ['morning', 'midday'], certifications: ['EPA 608'] },
  { id: 'kian', name: 'Kian Lee', availableShiftIds: ['morning', 'evening'], certifications: ['Fiber Splicing', 'Generator'] },
  { id: 'lina', name: 'Lina Ortiz', availableShiftIds: ['midday'], certifications: ['Water Systems'] },
  { id: 'amara', name: 'Amara Cole', availableShiftIds: ['morning', 'midday'], certifications: ['OSHA-30', 'First Aid'] },
  { id: 'jules', name: 'Jules Martin', availableShiftIds: ['midday', 'evening'], certifications: ['OSHA-30'] },
]);

const baselineSlots: readonly RoleSlot[] = Object.freeze([
  { id: 'm-n-electrical', shiftId: 'morning', zoneId: 'north', role: 'Electrical inspection', requiredCertifications: ['Electrical L2'], assignedTechnicianId: 'priya' },
  { id: 'm-n-safety', shiftId: 'morning', zoneId: 'north', role: 'Safety inspection', requiredCertifications: ['OSHA-30'], assignedTechnicianId: null },
  { id: 'm-c-hvac', shiftId: 'morning', zoneId: 'central', role: 'HVAC call', requiredCertifications: ['EPA 608'], assignedTechnicianId: 'theo' },
  { id: 'm-s-fiber', shiftId: 'morning', zoneId: 'south', role: 'Fiber repair', requiredCertifications: ['Fiber Splicing'], assignedTechnicianId: 'kian' },
  { id: 'd-n-water', shiftId: 'midday', zoneId: 'north', role: 'Water shutoff', requiredCertifications: ['Water Systems'], assignedTechnicianId: 'lina' },
  { id: 'd-c-safety', shiftId: 'midday', zoneId: 'central', role: 'Safety audit', requiredCertifications: ['OSHA-30'], assignedTechnicianId: 'amara' },
  { id: 'e-n-electrical', shiftId: 'evening', zoneId: 'north', role: 'Electrical restore', requiredCertifications: ['Electrical L2'], assignedTechnicianId: 'priya' },
  { id: 'e-s-generator', shiftId: 'evening', zoneId: 'south', role: 'Generator service', requiredCertifications: ['Generator'], assignedTechnicianId: 'kian' },
]);

export function cloneBaselineSlots(): RoleSlot[] {
  return baselineSlots.map((slot) => ({
    ...slot,
    requiredCertifications: [...slot.requiredCertifications],
  }));
}

export function getTechnician(id: string | null): Technician | undefined {
  return technicians.find((technician) => technician.id === id);
}

export function getShift(id: RoleSlot['shiftId']): Shift {
  return shifts.find((shift) => shift.id === id)!;
}

export function getZone(id: RoleSlot['zoneId']): Zone {
  return zones.find((zone) => zone.id === id)!;
}

export const baselineSnapshot = JSON.stringify(cloneBaselineSlots());
