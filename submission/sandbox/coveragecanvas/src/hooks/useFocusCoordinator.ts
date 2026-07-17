import { useEffect } from 'react';
import type { Dispatch } from 'react';
import type { PlannerAction } from '../domain/plannerReducer';
import type { FocusIntent } from '../domain/types';

export function useFocusCoordinator(focusIntent: FocusIntent, dispatch: Dispatch<PlannerAction>) {
  useEffect(() => {
    if (!focusIntent) return;
    const selector = focusIntent.target === 'undo'
      ? '[data-focus-target="undo"]'
      : focusIntent.target === 'first-technician'
        ? '[data-technician-id]:first-of-type'
        : `[data-focus-target="slot-${focusIntent.slotId}"]`;
    const control = document.querySelector<HTMLElement>(selector);
    control?.focus({ preventScroll: false, focusVisible: true });
    dispatch({ type: 'focus-complete' });
  }, [focusIntent, dispatch]);
}
