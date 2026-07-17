export type ShiftId = 'morning' | 'midday' | 'evening';
export type ZoneId = 'north' | 'central' | 'south';

export interface Shift {
  id: ShiftId;
  name: string;
  hours: string;
}

export interface Zone {
  id: ZoneId;
  name: string;
}

export interface RoleSlot {
  id: string;
  shiftId: ShiftId;
  zoneId: ZoneId;
  role: string;
  requiredCertifications: readonly string[];
  assignedTechnicianId: string | null;
}

export interface Technician {
  id: string;
  name: string;
  availableShiftIds: readonly ShiftId[];
  certifications: readonly string[];
}

export interface EligibilityResult {
  eligible: boolean;
  missingCertifications: string[];
  unavailableForShift: boolean;
  conflictingSlot: RoleSlot | null;
}

export interface Feedback {
  kind: 'error' | 'success';
  message: string;
}

export interface UndoEntry {
  slotId: string;
  previousTechnicianId: string | null;
  nextTechnicianId: string;
}

export type FocusIntent =
  | { target: 'undo'; nonce: number }
  | { target: 'first-technician'; nonce: number }
  | { target: 'slot'; slotId: string; nonce: number }
  | null;

export interface PlannerState {
  slots: RoleSlot[];
  selectedSlotId: string | null;
  selectedTechnicianId: string | null;
  feedback: Feedback | null;
  history: UndoEntry[];
  focusIntent: FocusIntent;
  nextFocusNonce: number;
}
