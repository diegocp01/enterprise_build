import type { CoverageTotals } from '../domain/coverage';

interface CoverageSummaryProps {
  totals: CoverageTotals;
  canUndo: boolean;
  onUndo: () => void;
  onReset: () => void;
}

export function CoverageSummary({ totals, canUndo, onUndo, onReset }: CoverageSummaryProps) {
  return (
    <section className="summary-bar" aria-label="Coverage summary">
      <div className="metric-group">
        <article className="metric metric-filled" data-testid="filled-total" aria-label={`Filled ${totals.filled} of ${totals.total} roles`}>
          <span>Filled</span><strong>{totals.filled}</strong><small>of {totals.total} roles</small>
        </article>
        <article className="metric metric-open" data-testid="uncovered-total" aria-label={`Uncovered ${totals.uncovered} needs dispatch`}>
          <span>Uncovered</span><strong>{totals.uncovered}</strong><small>needs dispatch</small>
        </article>
      </div>
      <div className="global-actions">
        <button className="button secondary" type="button" onClick={onUndo} disabled={!canUndo} data-focus-target="undo">
          Undo assignment
        </button>
        <button className="button ghost" type="button" onClick={onReset}>Reset plan</button>
      </div>
    </section>
  );
}
