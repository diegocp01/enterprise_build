import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import {
  DURATION, activeSegmentFor, appendActionAudit, createInitialState, evidenceRegistry, formatTime, historicalCalls, primaryCall,
  primarySegments, semanticCaseBrief, signalEvidenceIds, teams, type ActionDraft, type ActionSnapshot,
  type ActionAuditEntry, type ActionStatus, type Decision, type InvestigationState, type ReviewState, type Team,
} from './domain';

const MASK = '[REDACTED — email-like fictional value]';

function PrototypeNotice() {
  return <div className="prototype-notice" role="note">
    <div><strong>Deterministic prototype</strong><span>Transcription, sensitive candidates, classifications, recurrence, confidence values, and proposed actions are fictional fixed behavior requiring human review — not production AI.</span></div>
    <span className="local-badge"><span aria-hidden="true">●</span> Local only</span>
  </div>;
}

function StatePill({ state }: { state: string }) {
  const icon = state === 'confirmed' || state === 'Completed' ? '✓' : state === 'rejected' ? '×' : state === 'redacted' ? '◼' : state === 'restored' ? '↺' : state === 'In progress' ? '→' : state === 'Assigned' ? '◆' : '○';
  return <span className={`state-pill state-${state.toLowerCase().replaceAll(' ', '-')}`}><span aria-hidden="true">{icon}</span> {state[0].toUpperCase() + state.slice(1)}</span>;
}

function EvidenceMeta({ callId, segmentId, speaker, timestamp }: { callId: string; segmentId: string; speaker: string; timestamp: string }) {
  return <div className="evidence-meta">{callId} · {segmentId} · {speaker} · {timestamp}</div>;
}

function DecisionHistory({ title, history, empty = 'No human decisions yet' }: { title: string; history: Decision[]; empty?: string }) {
  return <details className="decision-history"><summary>{title}<span>{history.length} event{history.length === 1 ? '' : 's'}</span></summary>{history.length === 0 ? <p>{empty}</p> : <ol>{history.map(entry => <li key={`${entry.subjectId}-${entry.sequence}`}><span>#{String(entry.sequence).padStart(3, '0')}</span><div><strong>{entry.decision}</strong><small>{entry.subjectId} · {entry.reviewer} · {entry.previous} → {entry.result}</small></div></li>)}</ol>}</details>;
}

function ActionDecisionHistory({ history }: { history: ActionAuditEntry[] }) {
  return <details className="decision-history"><summary>Committed action history<span>{history.length} field change{history.length === 1 ? '' : 's'}</span></summary>{history.length === 0 ? <p>No committed field changes yet</p> : <ol>{history.map(entry => <li key={entry.sequence}><span>#{String(entry.sequence).padStart(3, '0')}</span><div><strong>{entry.field}</strong><small>{entry.subjectId} · {entry.reviewer} · {entry.previousValue} → {entry.resultingValue}</small></div></li>)}</ol>}</details>;
}

function Playback({ state, setState, onSeek }: { state: InvestigationState; setState: React.Dispatch<React.SetStateAction<InvestigationState>>; onSeek: (time: number, id: string) => void }) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [metadataValid, setMetadataValid] = useState(false);

  useLayoutEffect(() => {
    const audio = audioRef.current;
    if (audio && Number.isFinite(audio.duration) && Math.abs(audio.currentTime - state.currentTime) > .05) audio.currentTime = state.currentTime;
  }, [state.currentTime]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    if (state.playing) void audio.play().catch(() => setState(current => ({ ...current, playing: false })));
    else audio.pause();
  }, [setState, state.playing]);

  const syncFromNativeAudio = () => {
    const audio = audioRef.current;
    if (!audio) return;
    const currentTime = Math.min(DURATION, audio.currentTime);
    setState(current => ({ ...current, currentTime, selectedSegmentId: activeSegmentFor(currentTime).id, playing: !audio.paused && !audio.ended }));
  };

  return <section className="player-card" aria-labelledby="recording-title">
    <audio
      ref={audioRef}
      data-testid="bundled-audio"
      src={primaryCall.recording ?? undefined}
      preload="metadata"
      aria-label="Bundled 6 minute 18 second deterministic synthetic fictional call recording"
      onLoadedMetadata={event => setMetadataValid(Math.abs(event.currentTarget.duration - DURATION) < .01)}
      onTimeUpdate={syncFromNativeAudio}
      onPlay={syncFromNativeAudio}
      onPause={syncFromNativeAudio}
      onEnded={syncFromNativeAudio}
    />
    <div className="section-kicker">PRIMARY CALL</div>
    <div className="player-title-row"><div><h2 id="recording-title">Northstar Labs · EL-1042</h2><p>Fictional support call · 18 Jul 2026 · 06:18</p></div><span className="asset-badge">{metadataValid ? '✓ 06:18 bundled recording' : 'Bundled synthetic recording'}</span></div>
    <div className="player-controls">
      <button className="round-play" type="button" data-testid="play" aria-label={state.playing ? 'Pause fictional call' : 'Play fictional call'} onClick={() => setState(current => ({ ...current, playing: !current.playing }))}><span aria-hidden="true">{state.playing ? 'Ⅱ' : '▶'}</span></button>
      <div className="timeline-wrap"><label htmlFor="timeline" className="sr-only">Playback position</label><input id="timeline" data-testid="timeline" type="range" min="0" max={DURATION} step="1" value={Math.floor(state.currentTime)} aria-valuetext={`${formatTime(state.currentTime)} of ${formatTime(DURATION)}`} onChange={event => onSeek(Number(event.target.value), activeSegmentFor(Number(event.target.value)).id)} /><div className="timeline-meta"><output>{formatTime(state.currentTime)} / {formatTime(DURATION)}</output><span>Native audio position · fixed transcript boundaries</span></div></div>
    </div>
    <div className="markers" aria-label="Exact evidence markers"><button type="button" data-testid="complaint-marker" onClick={() => onSeek(151, 'SEG-14')}><span aria-hidden="true">◆</span> Complaint <strong>02:31</strong></button><button type="button" data-testid="commitment-marker" onClick={() => onSeek(252, 'SEG-22')}><span aria-hidden="true">◆</span> Commitment <strong>04:12</strong></button></div>
  </section>;
}

function Transcript({ state, onSeek }: { state: InvestigationState; onSeek: (time: number, id: string) => void }) {
  const active = activeSegmentFor(state.currentTime);
  const activeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => { activeRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }, [active.id]);
  return <section className="transcript-card" aria-labelledby="transcript-title" data-testid="transcript" data-active-segment={active.id}><div className="section-heading"><div><div className="section-kicker">CANONICAL EVIDENCE</div><h2 id="transcript-title">Synchronized transcript</h2></div><span className="transcript-count" aria-label="Ten fixed transcript segments">10 fixed segments</span></div>
    <div className="transcript-list">{primarySegments.map(segment => {
      const isActive = active.id === segment.id; const isSelected = state.selectedSegmentId === segment.id; const hidden = segment.kind === 'sensitive' && state.redaction === 'redacted';
      return <button ref={isActive ? activeRef : undefined} type="button" key={segment.id} className={`transcript-row ${isActive ? 'is-active' : ''} ${isSelected ? 'is-selected' : ''}`} aria-current={isActive ? 'true' : undefined} aria-label={`${segment.callId}, ${segment.id}, ${segment.speaker}, ${formatTime(segment.start)}. ${hidden ? MASK : segment.text}`} onClick={() => onSeek(segment.start, segment.id)}>
        <span className="speaker-col"><strong>{segment.speaker}</strong><small>{segment.id} · {formatTime(segment.start)}</small>{isActive && <span className="now-playing"><span aria-hidden="true">▶</span> Now playing</span>}</span>
        <span className="utterance">“{hidden ? MASK : segment.text}”{segment.kind && <span className={`inline-tag tag-${segment.kind}`}>{segment.kind === 'sensitive' ? 'Sensitive candidate' : segment.kind}</span>}</span>
      </button>;
    })}</div>
  </section>;
}

function FindingCard({ type, state, confidence, segmentId, onSeek, onReview }: { type: 'complaint' | 'commitment'; state: ReviewState; confidence: string; segmentId: string; onSeek: () => void; onReview: (next: ReviewState, focusId: string) => void }) {
  const segment = primarySegments.find(item => item.id === segmentId)!;
  return <article className="review-card"><div className="review-top"><StatePill state={state} /><span className="confidence">{confidence} fixed confidence</span></div><h3>{type[0].toUpperCase() + type.slice(1)} candidate</h3><blockquote>“{segment.text}”</blockquote><EvidenceMeta callId={segment.callId} segmentId={segment.id} speaker={segment.speaker} timestamp={formatTime(segment.start)} />
    <div className="button-row"><button type="button" onClick={onSeek}>Seek source</button><button type="button" id={`${type}-confirm`} data-testid={`${type}-confirm`} disabled={state === 'confirmed'} onClick={() => onReview('confirmed', `${type}-pending`)}>Confirm</button><button type="button" id={`${type}-reject`} data-testid={`${type}-reject`} disabled={state === 'rejected'} onClick={() => onReview('rejected', `${type}-pending`)}>Reject</button><button type="button" id={`${type}-pending`} data-testid={`${type}-pending`} disabled={state === 'pending'} onClick={() => onReview('pending', `${type}-confirm`)}>Return to pending</button></div>
  </article>;
}

function useControlledModal(dialogRef: React.RefObject<HTMLElement | null>, initialRef: React.RefObject<HTMLButtonElement | null>, generation: number, onEscape: () => void) {
  useLayoutEffect(() => {
    const focusTarget = () => initialRef.current?.focus({ preventScroll: true });
    focusTarget();
    const frame = window.requestAnimationFrame(focusTarget);
    return () => window.cancelAnimationFrame(frame);
  }, [generation, initialRef]);

  useLayoutEffect(() => {
    const app = document.querySelector('.app-shell');
    app?.setAttribute('inert', '');
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); onEscape(); return; }
      if (event.key !== 'Tab') return;
      const controls = [...(dialogRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])') ?? [])];
      if (!controls.length) { event.preventDefault(); return; }
      const first = controls[0]; const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => { app?.removeAttribute('inert'); document.removeEventListener('keydown', onKeyDown); };
  }, [dialogRef, onEscape]);
}

function HistoricalDialog({ callIndex, generation, onClose }: { callIndex: number; generation: number; onClose: (generation: number) => void }) {
  const call = historicalCalls[callIndex]; const closeRef = useRef<HTMLButtonElement>(null); const dialogRef = useRef<HTMLElement>(null);
  const citedEvidenceId = callIndex === 0 ? 'EV-1017-09' : 'EV-0998-18';
  useControlledModal(dialogRef, closeRef, generation, () => onClose(generation));
  return <div className="modal-backdrop" role="presentation"><section ref={dialogRef} className="dialog history-dialog" role="dialog" aria-modal="true" aria-labelledby="history-dialog-title" data-testid="historical-dialog"><div className="dialog-icon" aria-hidden="true">⌁</div><div className="section-kicker">COMPLETE BUNDLED HISTORICAL TRANSCRIPT</div><h2 id="history-dialog-title">{call.account} · {call.callId}</h2><p>{formatTime(call.durationSeconds)} fictional transcript fixture · cited evidence highlighted</p><div className="historical-transcript">{call.segments.map(segment => <article key={segment.evidenceId} className={segment.evidenceId === citedEvidenceId ? 'cited-segment' : ''}><EvidenceMeta callId={call.callId} segmentId={segment.id} speaker={segment.speaker} timestamp={formatTime(segment.start)} /><blockquote>“{segment.text}”</blockquote>{segment.evidenceId === citedEvidenceId && <span className="cited-label">Exact cited segment</span>}</article>)}</div><button ref={closeRef} data-testid="close-history" type="button" className="primary-button" onClick={() => onClose(generation)}>Close context</button></section></div>;
}

function ResetDialog({ generation, onCancel, onConfirm }: { generation: number; onCancel: (generation: number) => void; onConfirm: () => void }) {
  const confirmRef = useRef<HTMLButtonElement>(null); const dialogRef = useRef<HTMLElement>(null);
  useControlledModal(dialogRef, confirmRef, generation, () => onCancel(generation));
  return <div className="modal-backdrop" role="presentation"><section ref={dialogRef} className="dialog reset-dialog" role="dialog" aria-modal="true" aria-labelledby="reset-dialog-title" data-testid="reset-dialog"><div className="dialog-icon warning" aria-hidden="true">↺</div><div className="section-kicker">CONTROLLED RESET</div><h2 id="reset-dialog-title">Reset the fictional experience?</h2><p>This rebuilds every mutable field from the immutable fixture baseline. A downloaded brief stays on this device.</p><div className="button-row dialog-actions"><button data-testid="cancel-reset" type="button" onClick={() => onCancel(generation)}>Cancel</button><button ref={confirmRef} data-testid="confirm-reset" type="button" className="danger-button" onClick={onConfirm}>Confirm reset</button></div></section></div>;
}

function ActionEvidenceList() {
  return <ol>{signalEvidenceIds.map(evidenceId => { const evidence = evidenceRegistry[evidenceId]; return <li key={evidenceId}><strong>{evidence.callId} · {evidence.segmentId} · {evidence.speaker} · {evidence.timestamp}</strong><span>“{evidence.excerpt}”</span></li>; })}</ol>;
}

export default function App() {
  const [state, setState] = useState(createInitialState);
  const [historyDialog, setHistoryDialog] = useState<{ index: number; generation: number; opener: HTMLElement | null } | null>(null);
  const [resetDialog, setResetDialog] = useState<{ generation: number; opener: HTMLElement | null } | null>(null);
  const [status, setStatus] = useState('');
  const generationRef = useRef(0);
  const downloadUrlsRef = useRef<Array<{ attempt: number; url: string; timer: number }>>([]);
  const [downloadLifecycle, setDownloadLifecycle] = useState({ accepted: 0, revoked: 0 });
  const pendingFocusRef = useRef<HTMLElement | string | null>(null);

  const revokeDownload = (attempt: number) => {
    const record = downloadUrlsRef.current.find(item => item.attempt === attempt);
    if (!record) return false;
    window.clearTimeout(record.timer);
    URL.revokeObjectURL(record.url);
    downloadUrlsRef.current = downloadUrlsRef.current.filter(item => item !== record);
    return true;
  };
  const revokeAllDownloads = () => {
    for (const record of downloadUrlsRef.current) { window.clearTimeout(record.timer); URL.revokeObjectURL(record.url); }
    downloadUrlsRef.current = [];
  };
  useEffect(() => () => revokeAllDownloads(), []);
  useLayoutEffect(() => {
    const pending = pendingFocusRef.current;
    if (!pending) return;
    const target = typeof pending === 'string' ? document.getElementById(pending) : pending;
    if (!target || !target.isConnected || (target instanceof HTMLButtonElement && target.disabled)) return;
    const restore = () => {
      if (!target.isConnected || (target instanceof HTMLButtonElement && target.disabled)) return;
      target.focus({ preventScroll: true });
      if (document.activeElement === target) pendingFocusRef.current = null;
    };
    restore();
    // A closing modal removes `inert` during the same layout commit. Retry after
    // that cleanup so focus restoration cannot be lost to commit ordering.
    const frame = window.requestAnimationFrame(restore);
    return () => window.cancelAnimationFrame(frame);
  }, [state, historyDialog, resetDialog]);

  const seek = (time: number, id: string) => setState(current => ({ ...current, currentTime: time, selectedSegmentId: id, playing: false }));
  const appendDecision = (history: Decision[], subjectId: string, decision: string, previous: string, result: string, snapshots?: Pick<Decision, 'previousSnapshot' | 'resultingSnapshot'>): Decision[] => [...history, { sequence: history.length + 1, subjectId, reviewer: 'Casey Morgan', decision, previous, result, ...snapshots }];
  const focusAfter = (target: HTMLElement | string | null) => { pendingFocusRef.current = target; };
  const reviewFinding = (type: 'complaint' | 'commitment', next: ReviewState) => setState(current => ({ ...current, findings: { ...current.findings, [type]: next }, findingHistory: appendDecision(current.findingHistory, type === 'complaint' ? 'EV-014' : 'EV-022', `${type} ${next}`, current.findings[type], next) }));
  const reviewSignal = (next: ReviewState) => setState(current => ({ ...current, signal: next, signalHistory: appendDecision(current.signalHistory, 'SIG-03', `signal ${next}`, current.signal, next) }));
  const redact = (next: 'redacted' | 'restored') => setState(current => ({ ...current, redaction: next, redactionHistory: appendDecision(current.redactionHistory, 'EV-015', next === 'redacted' ? 'redact presentation' : 'restore presentation', current.redaction, next) }));

  const openHistory = (index: number, opener: HTMLElement) => { generationRef.current += 1; setHistoryDialog({ index, generation: generationRef.current, opener }); };
  const closeHistory = (generation: number) => setHistoryDialog(current => {
    if (!current || current.generation !== generation) return current;
    focusAfter(current.opener); return null;
  });
  const openReset = (opener: HTMLElement) => { generationRef.current += 1; setResetDialog({ generation: generationRef.current, opener }); };
  const cancelReset = (generation: number) => setResetDialog(current => {
    if (!current || current.generation !== generation) return current;
    focusAfter(current.opener); return null;
  });
  const confirmReset = () => {
    revokeAllDownloads(); setDownloadLifecycle({ accepted: 0, revoked: 0 });
    focusAfter(resetDialog?.opener ?? null); setState(createInitialState()); setResetDialog(null); setHistoryDialog(null);
    setStatus('Reset complete — the deterministic fictional baseline is restored.');
  };

  const updateActionDraft = <K extends keyof ActionDraft>(key: K, value: ActionDraft[K]) => setState(current => ({ ...current, action: { ...current.action, draft: { ...current.action.draft, [key]: value, ...(key === 'team' ? { owner: teams[value as Team][0] } : {}) } } }));
  const selectTeamFromKeyboard = (event: React.KeyboardEvent<HTMLSelectElement>) => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const match = (Object.keys(teams) as Team[]).find(team => team.toLowerCase().startsWith(event.key.toLowerCase()));
    if (!match || match === state.action.draft.team) return;
    event.preventDefault();
    updateActionDraft('team', match);
  };
  const actionValid = state.action.draft.evidenceIds.length === 3 && teams[state.action.draft.team].includes(state.action.draft.owner) && Boolean(state.action.draft.rationale.trim()) && Boolean(state.action.draft.status);
  const saveAction = () => {
    if (!actionValid) return;
    setState(current => {
      const previousSnapshot = current.action.committed;
      const resultingSnapshot: ActionSnapshot = { id: 'ACT-01', ...current.action.draft };
      return { ...current, action: { ...current.action, committed: resultingSnapshot, history: appendActionAudit(current.action.history, previousSnapshot, resultingSnapshot) } };
    });
    setStatus(`Action saved locally for ${state.action.draft.owner}. No external action was executed.`);
  };
  const exportBrief = () => {
    const attempt = state.exportAttempt + 1;
    const blob = new Blob([semanticCaseBrief(state)], { type: 'application/json' }); const url = URL.createObjectURL(blob);
    // Browsers expose no download-accepted callback to page script. Keep the URL
    // alive across a conservative handoff window, then revoke it automatically.
    // The real-browser gate proves each download completes before this fires.
    const timer = window.setTimeout(() => {
      if (!revokeDownload(attempt)) return;
      setDownloadLifecycle(current => ({ accepted: current.accepted + 1, revoked: current.revoked + 1 }));
      setStatus(`Export ${attempt} completed its local browser handoff — temporary Blob URL revoked.`);
    }, 4_000);
    downloadUrlsRef.current.push({ attempt, url, timer });
    const link = document.createElement('a'); link.href = url; link.download = 'EchoLedger_EL-1042_case-brief.json'; link.dataset.exportAttempt = String(attempt); document.body.append(link); link.click(); link.remove();
    setState(current => ({ ...current, exportAttempt: current.exportAttempt + 1 })); setStatus(`Export ${attempt} prepared locally — its temporary Blob URL remains live until correlated browser acceptance.`);
  };

  const committed = state.action.committed;
  return <>
    <div className="app-shell">
      <header className="topbar"><a className="brand" href="#top" aria-label="EchoLedger home"><span className="brand-mark" aria-hidden="true">E</span><span>EchoLedger<small>Evidence intelligence</small></span></a><div className="case-context"><span>ACTIVE INVESTIGATION</span><strong>EL-1042 · Northstar Labs</strong></div><div className="reviewer"><span className="avatar" aria-hidden="true">CM</span><span>Casey Morgan<small>Prototype reviewer</small></span></div></header>
      <PrototypeNotice />
      <main id="top" className="workspace">
        <section className="hero"><div><div className="section-kicker">CUSTOMER CONVERSATION / CASE EL-1042</div><h1>From the exact moment<br />to accountable action.</h1><p>Review one fictional support call, preserve its privacy treatment, and carry exact evidence into a human-owned follow-up.</p></div><div className="case-summary" aria-label="Three fictional calls, three exact evidence segments, zero external actions"><span><strong>3</strong> fictional calls</span><span><strong>3</strong> exact evidence segments</span><span><strong>0</strong> external actions</span></div></section>
        <div className="investigation-grid"><div className="primary-column"><Playback state={state} setState={setState} onSeek={seek} /><Transcript state={state} onSeek={seek} /></div>
          <aside className="evidence-rail" aria-labelledby="finding-review-title"><div className="rail-heading"><div><div className="section-kicker">HUMAN REVIEW</div><h2 id="finding-review-title">Finding queue</h2></div><span className="queue-count">2 candidates</span></div><p className="rail-note">Fixed classifications — inspect the exact source before deciding.</p>
            <FindingCard type="complaint" state={state.findings.complaint} confidence="0.91" segmentId="SEG-14" onSeek={() => seek(151, 'SEG-14')} onReview={(next, focusId) => { focusAfter(focusId); reviewFinding('complaint', next); }} />
            <FindingCard type="commitment" state={state.findings.commitment} confidence="0.84" segmentId="SEG-22" onSeek={() => seek(252, 'SEG-22')} onReview={(next, focusId) => { focusAfter(focusId); reviewFinding('commitment', next); }} />
            <DecisionHistory title="Finding decision history" history={state.findingHistory} />
          </aside></div>

        <section className="workflow-section privacy-section" aria-labelledby="privacy-title"><div className="section-intro"><div className="step-number">01</div><div><div className="section-kicker">PRIVACY TREATMENT</div><h2 id="privacy-title">Protect presentation. Preserve provenance.</h2><p>Mask the ordinary view without changing the stable evidence key or erasing earlier decisions.</p></div></div><div className="privacy-grid"><article className="feature-card"><div className="card-top"><StatePill state={state.redaction} /><span className="confidence">0.97 fixed confidence</span></div><h3>Email-like fictional value</h3><EvidenceMeta callId="EL-1042" segmentId="SEG-15" speaker="Mara Chen" timestamp="02:48" /><blockquote data-testid="sensitive-text">“{state.redaction === 'redacted' ? MASK : evidenceRegistry['EV-015'].excerpt}”</blockquote><p className="evidence-key">Stable identity <strong>EV-015</strong> · deterministic candidate</p><div className="button-row"><button id="redact-presentation" data-testid="redact" type="button" className="primary-button" disabled={state.redaction === 'redacted'} onClick={() => { focusAfter('restore-presentation'); redact('redacted'); }}>Redact presentation</button><button id="restore-presentation" data-testid="restore" type="button" disabled={state.redaction !== 'redacted'} onClick={() => { focusAfter('redact-presentation'); redact('restored'); }}>Restore presentation</button></div></article>
          <article className="history-card"><h3>Privacy decision history</h3><ol>{state.redactionHistory.map(entry => <li key={entry.sequence}><span>#{String(entry.sequence).padStart(3, '0')}</span><div><strong>{entry.decision}</strong><small>{entry.subjectId} · {entry.reviewer} · {entry.previous} → {entry.result}</small></div></li>)}</ol></article></div></section>

        <section className="workflow-section signal-section" aria-labelledby="signal-title"><div className="section-intro"><div className="step-number">02</div><div><div className="section-kicker">RECURRING PRODUCT SIGNAL</div><h2 id="signal-title">Trace the pattern before trusting it.</h2><p>A contestable hypothesis grounded in three complete fictional call contexts.</p></div></div><article className="signal-card"><div className="signal-summary"><div><StatePill state={state.signal} /><h3>Workspace export stalls near completion</h3><p>Larger fictional workspaces repeatedly stall during export near the final progress step.</p></div><div className="signal-metric" aria-label="Fixed confidence zero point eight eight"><strong>0.88</strong><span>fixed confidence</span></div></div><div className="rule-box"><strong>Deterministic basis · EXPORT_STALL_V1</strong><span>Matched “export” plus “stall” or “freeze” in three bundled fixtures. This is a review candidate, not a verified product fact.</span></div><div className="button-row signal-review"><button id="signal-confirm" type="button" data-testid="signal-confirm" disabled={state.signal === 'confirmed'} onClick={() => { focusAfter('signal-pending'); reviewSignal('confirmed'); }}>Confirm signal</button><button id="signal-reject" type="button" data-testid="signal-reject" disabled={state.signal === 'rejected'} onClick={() => { focusAfter('signal-pending'); reviewSignal('rejected'); }}>Reject signal</button><button id="signal-pending" type="button" data-testid="signal-pending" disabled={state.signal === 'pending'} onClick={() => { focusAfter('signal-confirm'); reviewSignal('pending'); }}>Return to pending</button></div>
          <DecisionHistory title="Signal decision history" history={state.signalHistory} />
          <div className="evidence-chain"><article><div className="evidence-index">01</div><div><span className="source-label">PRIMARY CALL</span><EvidenceMeta callId="EL-1042" segmentId="SEG-14" speaker="Mara Chen" timestamp="02:31" /><blockquote>“{evidenceRegistry['EV-014'].excerpt}”</blockquote><button type="button" onClick={() => seek(151, 'SEG-14')}>Seek exact source <span aria-hidden="true">→</span></button></div></article>{historicalCalls.map((call, index) => { const evidence = evidenceRegistry[index === 0 ? 'EV-1017-09' : 'EV-0998-18']; return <article key={call.callId}><div className="evidence-index">0{index + 2}</div><div><span className="source-label">HISTORICAL FIXTURE</span><EvidenceMeta callId={evidence.callId} segmentId={evidence.segmentId} speaker={evidence.speaker} timestamp={evidence.timestamp} /><blockquote>“{evidence.excerpt}”</blockquote><button type="button" data-testid={`history-${index}`} onClick={event => openHistory(index, event.currentTarget)}>Open complete bundled context <span aria-hidden="true">→</span></button></div></article>; })}</div></article></section>

        <section className="workflow-section action-section" aria-labelledby="action-title"><div className="section-intro"><div className="step-number">03</div><div><div className="section-kicker">ACCOUNTABLE FOLLOW-UP</div><h2 id="action-title">Name the owner. Keep the evidence beside them.</h2><p>Saving commits a browser-local proposal snapshot; draft edits never change the audit record until explicitly saved.</p></div></div><div className="action-grid"><form className="action-form" onSubmit={event => { event.preventDefault(); saveAction(); }}><div className="linked-signal"><span>LINKED SIGNAL</span><strong>SIG-03 · Workspace export stalls near completion</strong></div><label>Accountable team<select data-testid="team" value={state.action.draft.team} onKeyDown={selectTeamFromKeyboard} onChange={event => updateActionDraft('team', event.target.value as Team)}>{Object.keys(teams).map(team => <option key={team}>{team}</option>)}</select></label><label>Fictional owner<select data-testid="owner" value={state.action.draft.owner} onChange={event => updateActionDraft('owner', event.target.value)}>{teams[state.action.draft.team].map(owner => <option key={owner}>{owner}</option>)}</select></label><label>Rationale <span className="required">Required</span><textarea data-testid="rationale" value={state.action.draft.rationale} aria-describedby="action-validation action-help" onChange={event => updateActionDraft('rationale', event.target.value)} placeholder="Explain why this team should own the next review…" /></label><p id="action-help" className="field-help">Use evidence-specific language. This stays in browser memory.</p><label>Status<select data-testid="action-status" value={state.action.draft.status} onChange={event => updateActionDraft('status', event.target.value as ActionStatus)}><option>Assigned</option><option>In progress</option><option>Completed</option></select></label><p id="action-validation" data-testid="action-validation" className={actionValid ? 'field-valid' : 'form-error'}>{actionValid ? 'Ready to commit: signal, three evidence links, team, owner, rationale, and status are valid.' : 'Save requires the linked signal, three evidence items, an eligible team and owner, rationale, and status. Add a rationale to continue.'}</p><button data-testid="save-action" className="primary-button wide-button" type="submit" disabled={!actionValid}>{committed ? 'Update committed action' : 'Save accountable action'}</button>{committed && <div className="saved-action" data-testid="committed-action"><StatePill state={committed.status} /><strong>{committed.owner}</strong><span>{committed.team} · {state.action.history.length} ordered event{state.action.history.length === 1 ? '' : 's'}</span><p>Committed rationale: {committed.rationale}</p></div>}</form>
          <aside className="provenance-card"><div className="card-top"><div><div className="section-kicker">ACTION PROVENANCE</div><h3>Originating signal and three exact links</h3></div><span className="link-count" aria-label="Three linked evidence items">3</span></div><p className="provenance-signal"><strong>SIG-03</strong> · Workspace export stalls near completion</p><ActionEvidenceList /><ActionDecisionHistory history={state.action.history} /></aside></div></section>

        <section className="completion-section" aria-labelledby="complete-title"><div><div className="section-kicker">LOCAL COMPLETION</div><h2 id="complete-title">Leave with an audit trail.<br />Return to a clean baseline.</h2><p>The case brief preserves canonical evidence, masking state, full histories, review decisions, accountable ownership, and prototype boundaries.</p></div><div className="completion-actions"><article><span className="completion-icon" aria-hidden="true">↓</span><div><h3>Export case brief</h3><p>Consistently ordered JSON · browser-local Blob</p><button data-testid="export" type="button" className="primary-button" onClick={exportBrief}>Export local brief</button></div></article><article><span className="completion-icon reset" aria-hidden="true">↺</span><div><h3>Reset experience</h3><p>Rebuild every mutable field from fixtures</p><button data-testid="open-reset" type="button" className="danger-button" onClick={event => openReset(event.currentTarget)}>Reset fictional experience</button></div></article></div><p className="visible-status" role="status" data-testid="status">{status}</p><span className="sr-only" data-testid="download-lifecycle" data-live={downloadUrlsRef.current.length} data-accepted={downloadLifecycle.accepted} data-revoked={downloadLifecycle.revoked}>Temporary local download lifecycle</span></section>
      </main>
      <footer><span>EchoLedger · fictional deterministic prototype</span><span>No backend · No external integrations · No production AI</span></footer>
    </div>
    {historyDialog && <HistoricalDialog callIndex={historyDialog.index} generation={historyDialog.generation} onClose={closeHistory} />}
    {resetDialog && <ResetDialog generation={resetDialog.generation} onCancel={cancelReset} onConfirm={confirmReset} />}
  </>;
}
