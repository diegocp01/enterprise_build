import { getShift, getTechnician, getZone } from '../domain/fixture';
import type { RoleSlot } from '../domain/types';

interface RoleCardProps {
  slot: RoleSlot;
  onSelect: (slotId: string) => void;
}

export function RoleCard({ slot, onSelect }: RoleCardProps) {
  const assignee = getTechnician(slot.assignedTechnicianId);
  const zone = getZone(slot.zoneId);
  const shift = getShift(slot.shiftId);
  const uncovered = !assignee;
  return (
    <article className={`role-card ${uncovered ? 'is-uncovered' : 'is-filled'}`} data-slot-id={slot.id}>
      <div className="role-topline">
        <span className="zone-label">{shift.name} · {zone.name} zone</span>
        <span className={`status-label ${uncovered ? 'status-uncovered' : 'status-filled'}`}>
          {uncovered ? 'Uncovered' : 'Filled'}
        </span>
      </div>
      <h3>{slot.role}</h3>
      <p className="requirement"><span>Required</span>{slot.requiredCertifications.join(', ')}</p>
      {assignee ? (
        <div className="assignee"><span className="avatar" aria-hidden="true">{assignee.name.split(' ').map((part) => part[0]).join('')}</span><span><small>Assigned technician</small><strong>{assignee.name}</strong></span></div>
      ) : (
        <div className="uncovered-action">
          <p>Technician required for this shift.</p>
          <button
            type="button"
            className="button primary"
            onClick={() => onSelect(slot.id)}
            data-focus-target={`slot-${slot.id}`}
            aria-label={`Select technician for ${slot.role}, ${zone.name} zone`}
          >Select technician</button>
        </div>
      )}
    </article>
  );
}
