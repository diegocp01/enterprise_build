import { cloneBaselineSlots, getShift, getTechnician, getZone } from './fixture';
import { evaluateEligibility, rejectionMessage } from './eligibility';
import type { PlannerState } from './types';

export type PlannerAction =
  | { type: 'select-slot'; slotId: string }
  | { type: 'select-technician'; technicianId: string }
  | { type: 'commit'; slotId: string; technicianId: string }
  | { type: 'cancel-selection' }
  | { type: 'undo' }
  | { type: 'reset' }
  | { type: 'focus-complete' };

export function createInitialState(): PlannerState {
  return {
    slots: cloneBaselineSlots(),
    selectedSlotId: null,
    selectedTechnicianId: null,
    feedback: null,
    history: [],
    focusIntent: null,
    nextFocusNonce: 1,
  };
}

function reject(state: PlannerState, message: string): PlannerState {
  return { ...state, feedback: { kind: 'error', message } };
}

export function plannerReducer(state: PlannerState, action: PlannerAction): PlannerState {
  switch (action.type) {
    case 'select-slot': {
      const slot = state.slots.find((candidate) => candidate.id === action.slotId);
      if (!slot || slot.assignedTechnicianId) return state;
      return {
        ...state,
        selectedSlotId: slot.id,
        selectedTechnicianId: null,
        feedback: null,
        focusIntent: { target: 'first-technician', nonce: state.nextFocusNonce },
        nextFocusNonce: state.nextFocusNonce + 1,
      };
    }
    case 'select-technician':
      if (!state.selectedSlotId || !getTechnician(action.technicianId)) return state;
      return { ...state, selectedTechnicianId: action.technicianId, feedback: null };
    case 'commit': {
      if (state.selectedSlotId !== action.slotId || state.selectedTechnicianId !== action.technicianId) {
        return reject(state, 'Assignment not made. The selection changed before it could be confirmed. Review the role and technician, then try again.');
      }
      const slot = state.slots.find((candidate) => candidate.id === action.slotId);
      const technician = getTechnician(action.technicianId);
      if (!slot || !technician) return reject(state, 'Assignment not made. The selected role or technician is no longer available.');
      if (slot.assignedTechnicianId) return reject(state, `Assignment not made. ${slot.role} is already filled.`);

      const eligibility = evaluateEligibility(technician, slot, state.slots);
      if (!eligibility.eligible) return reject(state, rejectionMessage(technician, slot, eligibility));

      return {
        ...state,
        slots: state.slots.map((candidate) =>
          candidate.id === slot.id ? { ...candidate, assignedTechnicianId: technician.id } : candidate,
        ),
        selectedSlotId: null,
        selectedTechnicianId: null,
        feedback: {
          kind: 'success',
          message: `Assignment complete. ${technician.name} now covers ${slot.role} in ${getZone(slot.zoneId).name} during the ${getShift(slot.shiftId).name} shift.`,
        },
        history: [...state.history, { slotId: slot.id, previousTechnicianId: null, nextTechnicianId: technician.id }],
        focusIntent: { target: 'undo', nonce: state.nextFocusNonce },
        nextFocusNonce: state.nextFocusNonce + 1,
      };
    }
    case 'cancel-selection': {
      if (!state.selectedSlotId) return state;
      return {
        ...state,
        selectedSlotId: null,
        selectedTechnicianId: null,
        feedback: null,
        focusIntent: { target: 'slot', slotId: state.selectedSlotId, nonce: state.nextFocusNonce },
        nextFocusNonce: state.nextFocusNonce + 1,
      };
    }
    case 'undo': {
      const latest = state.history.at(-1);
      if (!latest) return state;
      const restoredSlot = state.slots.find((slot) => slot.id === latest.slotId);
      return {
        ...state,
        slots: state.slots.map((slot) =>
          slot.id === latest.slotId ? { ...slot, assignedTechnicianId: latest.previousTechnicianId } : slot,
        ),
        selectedSlotId: null,
        selectedTechnicianId: null,
        feedback: {
          kind: 'success',
          message: `${restoredSlot?.role ?? 'The latest role'} is uncovered again. The latest valid assignment was undone.`,
        },
        history: state.history.slice(0, -1),
        focusIntent: { target: 'slot', slotId: latest.slotId, nonce: state.nextFocusNonce },
        nextFocusNonce: state.nextFocusNonce + 1,
      };
    }
    case 'reset': {
      const baseline = createInitialState();
      return {
        ...baseline,
        focusIntent: { target: 'slot', slotId: 'm-n-safety', nonce: state.nextFocusNonce },
        nextFocusNonce: state.nextFocusNonce + 1,
      };
    }
    case 'focus-complete':
      return state.focusIntent ? { ...state, focusIntent: null } : state;
  }
}
