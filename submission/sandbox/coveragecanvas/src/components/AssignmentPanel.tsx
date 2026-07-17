import { useEffect } from 'react';
import { getShift, getZone, technicians } from '../domain/fixture';
import { eligibilitySummary, evaluateEligibility } from '../domain/eligibility';
import type { RoleSlot } from '../domain/types';

interface AssignmentPanelProps {
  slot: RoleSlot;
  slots: RoleSlot[];
  selectedTechnicianId: string | null;
  onSelectTechnician: (technicianId: string) => void;
  onCommit: () => void;
  onCancel: () => void;
}

export function AssignmentPanel({ slot, slots, selectedTechnicianId, onSelectTechnician, onCommit, onCancel }: AssignmentPanelProps) {
  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel();
    };
    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [onCancel]);

  const selectedTechnician = technicians.find((technician) => technician.id === selectedTechnicianId);
  const selectedEligibility = selectedTechnician ? evaluateEligibility(selectedTechnician, slot, slots) : null;
  return (
    <section className="assignment-panel" aria-labelledby="assignment-title" data-testid="assignment-panel">
      <div className="panel-intro">
        <div><span className="section-index">02</span><div><p className="kicker">Assignment desk</p><h2 id="assignment-title">Cover {slot.role}</h2></div></div>
        <button type="button" className="text-button" onClick={onCancel} data-focus-target="assignment-panel">Cancel <span aria-hidden="true">Esc</span></button>
      </div>
      <div className="selected-context" aria-label="Selected role details">
        <span><small>Shift</small><strong>{getShift(slot.shiftId).name}</strong></span>
        <span><small>Zone</small><strong>{getZone(slot.zoneId).name}</strong></span>
        <span><small>Certification</small><strong>{slot.requiredCertifications.join(', ')}</strong></span>
      </div>
      <fieldset>
        <legend>Choose a fictional technician</legend>
        <div className="technician-list">
          {technicians.map((technician) => {
            const result = evaluateEligibility(technician, slot, slots);
            return (
              <label className={`technician-option ${result.eligible ? 'option-eligible' : 'option-blocked'}`} key={technician.id}>
                <input
                  type="radio"
                  name="technician"
                  value={technician.id}
                  checked={selectedTechnicianId === technician.id}
                  onChange={() => onSelectTechnician(technician.id)}
                  data-technician-id={technician.id}
                />
                <span className="person-mark" aria-hidden="true">{technician.name.split(' ').map((part) => part[0]).join('')}</span>
                <span className="technician-copy">
                  <span className="technician-name">{technician.name}<em>{result.eligible ? 'Eligible' : 'Blocked'}</em></span>
                  <span className="tech-details">Available: {technician.availableShiftIds.map((id) => getShift(id).name).join(', ')} · Certifications: {technician.certifications.join(', ')}</span>
                  <span className="eligibility-copy">{eligibilitySummary(result, slot)}</span>
                </span>
              </label>
            );
          })}
        </div>
      </fieldset>
      <div className="panel-footer">
        <p>{selectedTechnician ? selectedEligibility?.eligible ? `${selectedTechnician.name} meets every requirement.` : 'This choice will be checked and explained before any change.' : 'Select a technician to review eligibility.'}</p>
        <button className="button primary commit-button" type="button" onClick={onCommit} disabled={!selectedTechnician}>
          {selectedTechnician ? `${selectedEligibility?.eligible ? 'Assign' : 'Attempt assignment to'} ${selectedTechnician.name}` : 'Choose a technician'}
        </button>
      </div>
    </section>
  );
}
