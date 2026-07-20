export type ReviewState = 'pending' | 'confirmed' | 'rejected';
export type RedactionState = 'candidate' | 'redacted' | 'restored';
export type Team = 'Customer Experience' | 'Product' | 'Support' | 'Legal';
export type ActionStatus = 'Assigned' | 'In progress' | 'Completed';

export interface Segment {
  id: string;
  evidenceId: string;
  callId: string;
  speaker: string;
  start: number;
  end: number;
  text: string;
  kind?: 'complaint' | 'sensitive' | 'commitment';
}

export interface CallFixture {
  callId: string;
  account: string;
  date: string;
  durationSeconds: number;
  recording: string | null;
  segments: readonly Segment[];
}

export interface EvidenceReference {
  evidenceId: string;
  callId: string;
  segmentId: string;
  speaker: string;
  start: number;
  end: number;
  timestamp: string;
  excerpt: string;
  sourceKind: 'primary-call' | 'historical-call';
}

export interface Decision {
  sequence: number;
  subjectId: string;
  reviewer: string;
  decision: string;
  previous: string;
  result: string;
  previousSnapshot?: ActionSnapshot | null;
  resultingSnapshot?: ActionSnapshot;
}

export interface ActionDraft {
  sourceSignal: 'SIG-03';
  evidenceIds: readonly ['EV-014', 'EV-1017-09', 'EV-0998-18'];
  team: Team;
  owner: string;
  rationale: string;
  status: ActionStatus;
}

export interface ActionSnapshot extends ActionDraft {
  id: 'ACT-01';
}

export type ActionAuditField = 'team' | 'owner' | 'rationale' | 'evidenceIds' | 'status';

export interface ActionAuditEntry {
  sequence: number;
  subjectId: 'ACT-01';
  reviewer: 'Casey Morgan';
  field: ActionAuditField;
  previousValue: string;
  resultingValue: string;
}

export interface InvestigationState {
  currentTime: number;
  playing: boolean;
  selectedSegmentId: string;
  redaction: RedactionState;
  redactionHistory: Decision[];
  findings: Record<'complaint' | 'commitment', ReviewState>;
  findingHistory: Decision[];
  signal: ReviewState;
  signalHistory: Decision[];
  action: {
    draft: ActionDraft;
    committed: ActionSnapshot | null;
    history: ActionAuditEntry[];
  };
  exportAttempt: number;
}

export const DURATION = 378;

const freezeSegments = (segments: readonly Segment[]): readonly Segment[] => {
  const frozenSegments: Segment[] = segments.map(segment => Object.freeze({ ...segment }) as Segment);
  return Object.freeze(frozenSegments);
};

const primarySegmentsFixture = freezeSegments([
  { id: 'SEG-01', evidenceId: 'EV-001', callId: 'EL-1042', speaker: 'Jon Bell', start: 0, end: 32, text: 'Thanks for calling Northstar support. I have opened a fictional diagnostic workspace for this review.' },
  { id: 'SEG-05', evidenceId: 'EV-005', callId: 'EL-1042', speaker: 'Mara Chen', start: 32, end: 76, text: 'Our operations group is preparing the quarterly workspace archive.' },
  { id: 'SEG-08', evidenceId: 'EV-008', callId: 'EL-1042', speaker: 'Jon Bell', start: 76, end: 136, text: 'I will walk through the bundled export checklist with you.' },
  { id: 'SEG-13', evidenceId: 'EV-013', callId: 'EL-1042', speaker: 'Mara Chen', start: 136, end: 151, text: 'I restarted the workspace twice before calling.' },
  { id: 'SEG-14', evidenceId: 'EV-014', callId: 'EL-1042', speaker: 'Mara Chen', start: 151, end: 168, text: 'Every time our team exports the quarterly workspace, the progress reaches ninety-eight percent and then stalls.', kind: 'complaint' },
  { id: 'SEG-15', evidenceId: 'EV-015', callId: 'EL-1042', speaker: 'Mara Chen', start: 168, end: 202, text: 'You can reach me at mara.chen@example.test after the review.', kind: 'sensitive' },
  { id: 'SEG-18', evidenceId: 'EV-018', callId: 'EL-1042', speaker: 'Jon Bell', start: 202, end: 252, text: 'The fictional diagnostic log shows the export reaching its final packaging step.' },
  { id: 'SEG-22', evidenceId: 'EV-022', callId: 'EL-1042', speaker: 'Jon Bell', start: 252, end: 281, text: 'I will send the export diagnostics checklist by four this afternoon.', kind: 'commitment' },
  { id: 'SEG-25', evidenceId: 'EV-025', callId: 'EL-1042', speaker: 'Mara Chen', start: 281, end: 333, text: 'Please include the exact final-step checks so our team can compare the next attempt.' },
  { id: 'SEG-29', evidenceId: 'EV-029', callId: 'EL-1042', speaker: 'Jon Bell', start: 333, end: 378, text: 'I have captured that request in this local fictional case.' },
]);

const historicalCallFixtures: readonly CallFixture[] = Object.freeze([
  Object.freeze({
    callId: 'EL-1017', account: 'Harborline Systems', date: '2026-07-11', durationSeconds: 302, recording: null,
    segments: freezeSegments([
      { id: 'SEG-01', evidenceId: 'EV-1017-01', callId: 'EL-1017', speaker: 'Nia Ford', start: 0, end: 44, text: 'This is a bundled fictional follow-up about the operations workspace archive.' },
      { id: 'SEG-04', evidenceId: 'EV-1017-04', callId: 'EL-1017', speaker: 'Imani Reed', start: 44, end: 103, text: 'We tested the archive twice in the local training fixture and saw the same final-step behavior.' },
      { id: 'SEG-07', evidenceId: 'EV-1017-07', callId: 'EL-1017', speaker: 'Nia Ford', start: 103, end: 188, text: 'I am documenting each fictional attempt and the progress stage where it stopped.' },
      { id: 'SEG-09', evidenceId: 'EV-1017-09', callId: 'EL-1017', speaker: 'Imani Reed', start: 188, end: 222, text: 'The archive export froze on the final step for our operations workspace.' },
      { id: 'SEG-12', evidenceId: 'EV-1017-12', callId: 'EL-1017', speaker: 'Nia Ford', start: 222, end: 268, text: 'No customer data leaves this local prototype while we review the fixture.' },
      { id: 'SEG-15', evidenceId: 'EV-1017-15', callId: 'EL-1017', speaker: 'Imani Reed', start: 268, end: 302, text: 'Please attach this fictional example to the product review.' },
    ]),
  }),
  Object.freeze({
    callId: 'EL-0998', account: 'Atlas Ridge Group', date: '2026-06-29', durationSeconds: 364, recording: null,
    segments: freezeSegments([
      { id: 'SEG-01', evidenceId: 'EV-0998-01', callId: 'EL-0998', speaker: 'Samira Holt', start: 0, end: 62, text: 'I am opening the bundled fictional monthly-export case for local review.' },
      { id: 'SEG-05', evidenceId: 'EV-0998-05', callId: 'EL-0998', speaker: 'Tomas Vale', start: 62, end: 141, text: 'The fixture represents our largest monthly workspace and a routine archive attempt.' },
      { id: 'SEG-09', evidenceId: 'EV-0998-09', callId: 'EL-0998', speaker: 'Samira Holt', start: 141, end: 228, text: 'I have recorded the deterministic progress checkpoints and the cancellation outcome.' },
      { id: 'SEG-13', evidenceId: 'EV-0998-13', callId: 'EL-0998', speaker: 'Tomas Vale', start: 228, end: 305, text: 'The earlier local fixture completed, but this monthly example did not reach a finished archive.' },
      { id: 'SEG-18', evidenceId: 'EV-0998-18', callId: 'EL-0998', speaker: 'Tomas Vale', start: 341, end: 364, text: 'Our monthly export stalled at ninety-nine percent until we cancelled it.' },
      { id: 'SEG-16', evidenceId: 'EV-0998-16', callId: 'EL-0998', speaker: 'Samira Holt', start: 305, end: 341, text: 'I will route this fictional example to a named reviewer with its exact evidence.' },
    ].sort((a, b) => a.start - b.start)),
  }),
]);

export const primaryCall: CallFixture = Object.freeze({
  callId: 'EL-1042', account: 'Northstar Labs', date: '2026-07-18', durationSeconds: DURATION,
  recording: '/audio/echoledger-el1042.wav', segments: primarySegmentsFixture,
});

export const primarySegments = primaryCall.segments;
export const historicalCalls = historicalCallFixtures;
export const allCalls: readonly CallFixture[] = Object.freeze([primaryCall, ...historicalCalls]);

export const teams: Readonly<Record<Team, readonly string[]>> = Object.freeze({
  'Customer Experience': Object.freeze(['Avery Solis', 'Morgan Pike']),
  Product: Object.freeze(['Priya Nolen', 'Eli Navarro']),
  Support: Object.freeze(['Jon Bell', 'Samira Holt']),
  Legal: Object.freeze(['Noah Perrin', 'Leena Shah']),
});

export const formatTime = (seconds: number) => `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`;

export const activeSegmentFor = (time: number) => primarySegments.find(segment => time >= segment.start && time < segment.end) ?? primarySegments[primarySegments.length - 1];

const evidenceEntries = allCalls.flatMap(call => call.segments.map(segment => [segment.evidenceId, Object.freeze({
  evidenceId: segment.evidenceId,
  callId: segment.callId,
  segmentId: segment.id,
  speaker: segment.speaker,
  start: segment.start,
  end: segment.end,
  timestamp: formatTime(segment.start),
  excerpt: segment.text,
  sourceKind: call.callId === primaryCall.callId ? 'primary-call' as const : 'historical-call' as const,
})] as const));

export const evidenceRegistry: Readonly<Record<string, EvidenceReference>> = Object.freeze(Object.fromEntries(evidenceEntries));
export const signalEvidenceIds = Object.freeze(['EV-014', 'EV-1017-09', 'EV-0998-18'] as const);

const actionAuditFields: readonly ActionAuditField[] = Object.freeze(['team', 'owner', 'rationale', 'evidenceIds', 'status']);

const actionAuditValue = (snapshot: ActionSnapshot | null, field: ActionAuditField) => {
  if (!snapshot) return field === 'evidenceIds' ? '[]' : 'not committed';
  return field === 'evidenceIds' ? JSON.stringify(snapshot.evidenceIds) : String(snapshot[field]);
};

export function appendActionAudit(history: ActionAuditEntry[], previous: ActionSnapshot | null, resulting: ActionSnapshot): ActionAuditEntry[] {
  const changes = actionAuditFields.flatMap(field => {
    const previousValue = actionAuditValue(previous, field);
    const resultingValue = actionAuditValue(resulting, field);
    return previousValue === resultingValue ? [] : [{ field, previousValue, resultingValue }];
  });
  return [...history, ...changes.map((change, index): ActionAuditEntry => ({
    sequence: history.length + index + 1,
    subjectId: 'ACT-01',
    reviewer: 'Casey Morgan',
    ...change,
  }))];
}

const initialDraft = (): ActionDraft => ({
  sourceSignal: 'SIG-03', evidenceIds: signalEvidenceIds, team: 'Product', owner: 'Priya Nolen', rationale: '', status: 'Assigned',
});

export function createInitialState(): InvestigationState {
  return {
    currentTime: 0,
    playing: false,
    selectedSegmentId: 'SEG-01',
    redaction: 'candidate',
    redactionHistory: [{ sequence: 1, subjectId: 'EV-015', reviewer: 'Fixture generator', decision: 'candidate identified', previous: 'none', result: 'candidate' }],
    findings: { complaint: 'pending', commitment: 'pending' },
    findingHistory: [],
    signal: 'pending',
    signalHistory: [],
    action: { draft: initialDraft(), committed: null, history: [] },
    exportAttempt: 0,
  };
}

const resolveEvidence = (evidenceId: string) => evidenceRegistry[evidenceId];
const fullTranscript = (call: CallFixture) => call.segments.map(segment => ({
  segmentId: segment.id, evidenceId: segment.evidenceId, speaker: segment.speaker, start: segment.start, end: segment.end,
  timestamp: formatTime(segment.start), excerpt: segment.text,
}));

export function serializeCaseBrief(state: InvestigationState) {
  const sensitiveEvidence = resolveEvidence('EV-015');
  const committedAction = state.action.committed;
  return {
    schema: 'echoledger-case-brief-v2',
    generatedFrom: 'deterministic-local-state',
    prototypeBoundaries: ['Fictional deterministic fixtures only', 'Pre-authored transcription and classifications require human review', 'No production AI or external action', 'Browser-local export only'],
    calls: allCalls.map(call => ({ id: call.callId, account: call.account, date: call.date, durationSeconds: call.durationSeconds, recording: call.recording ? 'bundled synthetic fictional recording' : 'transcript-only historical fixture', transcript: fullTranscript(call) })),
    evidenceCatalog: Object.keys(evidenceRegistry).sort().map(resolveEvidence),
    privacy: {
      evidenceId: sensitiveEvidence.evidenceId,
      evidence: sensitiveEvidence,
      presentation: state.redaction,
      displayedText: state.redaction === 'redacted' ? '[REDACTED — email-like fictional value]' : sensitiveEvidence.excerpt,
      history: state.redactionHistory,
    },
    findings: [
      { id: 'FIND-01', type: 'complaint', confidence: 0.91, review: state.findings.complaint, evidence: resolveEvidence('EV-014') },
      { id: 'FIND-02', type: 'commitment', confidence: 0.84, review: state.findings.commitment, evidence: resolveEvidence('EV-022') },
    ],
    findingHistory: state.findingHistory,
    signal: { id: 'SIG-03', hypothesis: 'Workspace export stalls near completion', confidence: 0.88, rule: 'EXPORT_STALL_V1', review: state.signal, evidence: signalEvidenceIds.map(resolveEvidence), history: state.signalHistory },
    action: committedAction ? { ...committedAction, evidence: committedAction.evidenceIds.map(resolveEvidence), history: state.action.history } : null,
  };
}

export const semanticCaseBrief = (state: InvestigationState) => JSON.stringify(serializeCaseBrief(state), null, 2);
