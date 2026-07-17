import { getShift, shifts } from '../domain/fixture';
import type { RoleSlot } from '../domain/types';
import { RoleCard } from './RoleCard';

interface CoverageMatrixProps {
  slots: RoleSlot[];
  onSelect: (slotId: string) => void;
}

export function CoverageMatrix({ slots, onSelect }: CoverageMatrixProps) {
  return (
    <section className="coverage-board" aria-labelledby="coverage-title">
      <div className="section-heading">
        <div><span className="section-index">01</span><div><p className="kicker">Coverage matrix</p><h2 id="coverage-title">Today’s service plan</h2></div></div>
        <p>All people and assignments are fictional.</p>
      </div>
      <div className="shift-grid">
        {shifts.map((shift) => {
          const shiftSlots = slots.filter((slot) => slot.shiftId === shift.id);
          return (
            <section className="shift-column" key={shift.id} aria-labelledby={`shift-${shift.id}`}>
              <header className="shift-heading">
                <div><span className="shift-dot" aria-hidden="true" /><h2 id={`shift-${shift.id}`}>{getShift(shift.id).name}</h2></div>
                <span>{shift.hours}</span>
              </header>
              <div className="shift-roles">{shiftSlots.map((slot) => <RoleCard slot={slot} onSelect={onSelect} key={slot.id} />)}</div>
            </section>
          );
        })}
      </div>
    </section>
  );
}
