import { useCallback, useMemo, useReducer } from 'react';
import { AssignmentPanel } from './components/AssignmentPanel';
import { CoverageMatrix } from './components/CoverageMatrix';
import { CoverageSummary } from './components/CoverageSummary';
import { FeedbackRegion } from './components/FeedbackRegion';
import { getCoverageTotals } from './domain/coverage';
import { createInitialState, plannerReducer } from './domain/plannerReducer';
import { useFocusCoordinator } from './hooks/useFocusCoordinator';

export default function App() {
  const [state, dispatch] = useReducer(plannerReducer, undefined, createInitialState);
  const totals = useMemo(() => getCoverageTotals(state.slots), [state.slots]);
  const selectedSlot = state.slots.find((slot) => slot.id === state.selectedSlotId) ?? null;
  const cancelSelection = useCallback(() => dispatch({ type: 'cancel-selection' }), []);
  useFocusCoordinator(state.focusIntent, dispatch);

  return (
    <main>
      <header className="app-header">
        <a className="brand" href="#coverage-title" aria-label="CoverageCanvas, skip to coverage matrix">
          <span className="brand-mark" aria-hidden="true"><i /><i /><i /><i /></span>
          <span>Coverage<span>Canvas</span></span>
        </a>
        <div className="day-context"><span className="pulse" aria-hidden="true" />Planning workspace <strong>Tuesday · Fictional service day</strong></div>
      </header>

      <section className="hero" aria-labelledby="page-title">
        <div><p className="kicker">Daily dispatch · Local-only demo</p><h1 id="page-title">Put every shift<br /><em>within reach.</em></h1></div>
        <p>Review eight field-service roles, resolve coverage gaps with clear eligibility evidence, and recover changes with confidence.</p>
      </section>

      <CoverageSummary totals={totals} canUndo={state.history.length > 0} onUndo={() => dispatch({ type: 'undo' })} onReset={() => dispatch({ type: 'reset' })} />
      <FeedbackRegion feedback={state.feedback} />

      {selectedSlot && (
        <AssignmentPanel
          slot={selectedSlot}
          slots={state.slots}
          selectedTechnicianId={state.selectedTechnicianId}
          onSelectTechnician={(technicianId) => dispatch({ type: 'select-technician', technicianId })}
          onCommit={() => state.selectedTechnicianId && dispatch({ type: 'commit', slotId: selectedSlot.id, technicianId: state.selectedTechnicianId })}
          onCancel={cancelSelection}
        />
      )}

      <CoverageMatrix slots={state.slots} onSelect={(slotId) => dispatch({ type: 'select-slot', slotId })} />
      <footer><p>CoverageCanvas uses deterministic fictional data. Nothing is saved or sent.</p><span>8 roles · 3 shifts · 3 zones</span></footer>
    </main>
  );
}
