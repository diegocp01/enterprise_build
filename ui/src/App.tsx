import { useCallback, useEffect, useMemo, useState } from 'react';

type View = 'setup' | 'live' | 'evidence' | 'delivery';
type RunStatus = 'created' | 'running' | 'repairing' | 'completed' | 'failed' | 'cancelled' | 'unknown';

interface RunSummary {
  run_id: string;
  status: RunStatus;
  current_stage?: string;
  completed_stages: string[];
  relationship_vector_digest?: string;
  adapter?: string;
  build_request?: {
    idea: string;
    audience: string;
    outcome: string;
    constraints: string[];
    must_have_capabilities: string[];
  };
  repair_count: number;
  gate_count: number;
  agent_call_count: number;
  event_count: number;
  failure_reason?: string;
  bundle_ready: boolean;
  preview_ready: boolean;
  video_ready: boolean;
}

interface Artifact {
  artifact_id: string;
  artifact_type: string;
  stage: string;
  version: number;
  lead: string;
  peer: string;
  gate_status: string;
  content_digest: string;
  contract_item_ids?: string[];
  requirement_ids?: string[];
}

interface Evidence {
  artifacts: Artifact[];
  gates: Array<{ stage: string; decision: string; findings?: unknown[] }>;
  repairs: Array<{ repair_id: string; stage: string; attempt: number }>;
  commands: Array<{ stage?: string; command?: string[]; exit_code?: number }>;
  agent_calls: Array<{ stage: string; actor?: string; agent?: string; status: string; duration_ms?: number }>;
  demo: Array<{ status: string; duration_seconds?: number; checksum?: string }>;
}

interface Doctor {
  ok: boolean;
  checks: Record<string, { ok?: boolean; available?: boolean; version?: string }>;
  frozen_snapshot: { ready: boolean; path?: string };
}

const stages = [
  ['SENSE', 'Opportunity Model'],
  ['MODEL', 'Outcome Model'],
  ['COMPOSE', 'Capability Graph'],
  ['DECIDE', 'Decision Graph'],
  ['SIMULATE', 'Scenario Model'],
  ['EXECUTE', 'Autonomous Change'],
  ['OBSERVE', 'Evidence + Learning'],
] as const;

const emptyEvidence: Evidence = { artifacts: [], gates: [], repairs: [], commands: [], agent_calls: [], demo: [] };

async function getJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

function Mark({ ok }: { ok: boolean }) {
  return <span className={`mark ${ok ? 'ok' : 'waiting'}`} aria-hidden="true">{ok ? '✓' : '·'}</span>;
}

function StatusPill({ status }: { status: string }) {
  return <span className={`status status-${status.toLowerCase()}`}><span />{status.replaceAll('_', ' ')}</span>;
}

function SetupView({ doctor, run }: { doctor?: Doctor; run?: RunSummary }) {
  const request = run?.build_request;
  return <section className="view-grid setup-grid" aria-labelledby="setup-title">
    <div className="intro-panel">
      <span className="kicker">One request · zero handoffs</span>
      <h2 id="setup-title">Start in Codex. Watch everything here.</h2>
      <p>Give Codex one short build request. Seven trained two-agent cells carry it from definition to a verified delivery bundle while this Control Room remains observational.</p>
      <div className="readiness-card">
        <div><Mark ok={Boolean(doctor?.ok)} /><span>Local runtime</span><small>{doctor?.ok ? 'Ready' : 'Checking'}</small></div>
        <div><Mark ok={Boolean(doctor?.frozen_snapshot.ready)} /><span>Trust baseline</span><small>{doctor?.frozen_snapshot.ready ? 'Verified' : 'Train first'}</small></div>
        <div><Mark ok={Boolean(doctor?.checks.codex?.available)} /><span>Codex adapter</span><small>{doctor?.checks.codex?.version || 'Checking'}</small></div>
      </div>
    </div>
    <article className="request-card">
      <div className="card-head"><div><span className="kicker">Build request</span><h3>{request ? 'Captured by the chat workflow' : 'Use the Codex chat workflow'}</h3></div><span className="step-chip">01</span></div>
      {request ? <div className="captured-request">
        <div className="captured-field"><span>Product idea</span><p>{request.idea}</p></div>
        <div className="field-row">
          <div className="captured-field"><span>Audience</span><p>{request.audience}</p></div>
          <div className="captured-field"><span>Desired outcome</span><p>{request.outcome}</p></div>
        </div>
        <div className="captured-field"><span>Must-have capabilities</span><p>{request.must_have_capabilities.join(' · ') || 'Defined autonomously from the request'}</p></div>
        <div className="captured-field"><span>Constraints</span><p>{request.constraints.join(' · ') || 'Local React/Vite delivery profile'}</p></div>
      </div> : <div className="chat-start">
        <p>In this repository’s Codex chat, invoke:</p>
        <code className="chat-command">$run-pipeline</code>
        <p className="chat-note">Codex collects the idea, audience, outcome, and constraints, verifies the trained trust baseline, then starts the run. No terminal command or UI approval is required.</p>
      </div>}
    </article>
  </section>;
}

function LiveView({ run, artifacts, onCancel }: { run?: RunSummary; artifacts: Artifact[]; onCancel: () => void }) {
  if (!run) return <Empty title="No active run" copy="Start a build request to watch the seven delivery cells work." />;
  const latestByStage = new Map<string, Artifact>();
  artifacts.forEach(artifact => {
    const current = latestByStage.get(artifact.stage);
    if (!current || artifact.version > current.version) latestByStage.set(artifact.stage, artifact);
  });
  const complete = new Set(run.completed_stages);
  return <section aria-labelledby="live-title">
    <div className="run-hero">
      <div><span className="kicker">Live orchestration</span><h2 id="live-title">The lifecycle moves. The trained baseline stays intact.</h2><p className="mono">{run.run_id}</p></div>
      <div className="hero-actions"><StatusPill status={run.status} />{run.status === 'running' && <button className="danger" onClick={onCancel}>Emergency stop</button>}</div>
    </div>
    <div className="metric-strip">
      <div><strong>{Array.from(complete).filter(stage => stages.some(([name]) => name === stage)).length}<span>/7</span></strong><small>Cells complete</small></div>
      <div><strong>{run.agent_call_count}</strong><small>Agent calls</small></div>
      <div><strong>{run.gate_count}</strong><small>Gate decisions</small></div>
      <div><strong>{run.repair_count}</strong><small>Bounded repairs</small></div>
    </div>
    <div className="pipeline" role="list" aria-label="Delivery stages">
      {stages.map(([stage, output], index) => {
        const artifact = latestByStage.get(stage);
        const isDone = complete.has(stage);
        const isActive = run.current_stage === stage && run.status === 'running';
        return <article className={`stage-card ${isDone ? 'done' : ''} ${isActive ? 'active' : ''}`} role="listitem" key={stage}>
          <div className="stage-index">{String(index + 1).padStart(2, '0')}</div>
          <div><span className="kicker">{output}</span><h3>{stage}</h3></div>
          <div className="cell-pair"><span>{artifact?.lead?.slice(0, 1) || 'A'}</span><i>↔</i><span>{artifact?.peer?.slice(0, 1) || 'B'}</span></div>
          <div className="lead-line">{artifact ? <><b>{artifact.lead}</b> led · {artifact.peer} reviewed</> : isActive ? 'Two proposals in progress' : 'Waiting for verified input'}</div>
          <StatusPill status={isDone ? 'PASS' : isActive ? 'RUNNING' : 'QUEUED'} />
        </article>;
      })}
    </div>
    {run.failure_reason && <p className="error" role="alert">{run.failure_reason}</p>}
  </section>;
}

function EvidenceView({ run, evidence }: { run?: RunSummary; evidence: Evidence }) {
  if (!run) return <Empty title="No evidence yet" copy="Every call, artifact, gate, repair, and command will appear here." />;
  return <section aria-labelledby="evidence-title">
    <div className="run-hero compact"><div><span className="kicker">Audit trail</span><h2 id="evidence-title">Proof, not promises.</h2></div><div className="digest"><span>Frozen vector digest</span><code>{run.relationship_vector_digest?.slice(0, 28)}…</code></div></div>
    <div className="evidence-layout">
      <div className="evidence-main">
        <div className="section-head"><h3>Versioned artifacts</h3><span>{evidence.artifacts.length} committed</span></div>
        <div className="artifact-table" role="table">
          {evidence.artifacts.length === 0 && <p className="muted">Artifacts appear after the first gate.</p>}
          {evidence.artifacts.map((artifact, index) => <div className="artifact-row" role="row" key={`${artifact.stage}-${artifact.version}-${index}`}>
            <span className="artifact-stage">{artifact.stage}</span><div><b>{artifact.artifact_type.replaceAll('_', ' ')}</b><small>{artifact.contract_item_ids?.length ?? artifact.requirement_ids?.length ?? 0} outcome items traced</small></div><span>v{artifact.version}</span><StatusPill status={artifact.gate_status} /><code>{artifact.content_digest.slice(7, 15)}</code>
          </div>)}
        </div>
      </div>
      <aside className="evidence-side">
        <div className="evidence-stat"><span>Gate ledger</span><strong>{evidence.gates.length}</strong><small>{evidence.gates.filter(gate => gate.decision === 'PASS').length} passing decisions</small></div>
        <div className="evidence-stat coral"><span>Repair ledger</span><strong>{evidence.repairs.length}</strong><small>Original evidence is never overwritten</small></div>
        <div className="evidence-stat blue"><span>Raw calls</span><strong>{evidence.agent_calls.length}</strong><small>Append-only JSONL records</small></div>
      </aside>
    </div>
  </section>;
}

function DeliveryView({ run }: { run?: RunSummary }) {
  if (!run?.bundle_ready) return <Empty title="Delivery is being assembled" copy="The capability, evidence, checksums, logs, and narrated demo arrive together after OBSERVE passes." />;
  return <section aria-labelledby="delivery-title">
    <div className="run-hero compact"><div><span className="kicker">Delivered automatically</span><h2 id="delivery-title">A runnable bundle, ready to inspect.</h2></div><a className="primary link-button" href={`/api/runs/${run.run_id}/bundle`}>Download bundle <span>↓</span></a></div>
    <div className="delivery-grid">
      <article className="preview-card"><div className="browser-bar"><span /><span /><span /><b>Generated application</b></div><iframe title="Generated application preview" src={`/api/runs/${run.run_id}/preview`} /></article>
      <article className="video-card"><span className="kicker">Narrated evidence</span><h3>The autonomous run, in one short demo.</h3><video controls preload="metadata" src={`/api/runs/${run.run_id}/video`} /><div className="delivery-checks"><span><Mark ok />Runnable app</span><span><Mark ok />Tests + gates</span><span><Mark ok />JSON logs</span><span><Mark ok />Checksums</span></div></article>
    </div>
  </section>;
}

function Empty({ title, copy }: { title: string; copy: string }) {
  return <section className="empty-state"><span className="empty-mark">Z</span><h2>{title}</h2><p>{copy}</p></section>;
}

export default function App() {
  const [view, setView] = useState<View>('setup');
  const [doctor, setDoctor] = useState<Doctor>();
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selectedRun, setSelectedRun] = useState<string>(() => localStorage.getItem('zerohandoff-run') || '');
  const [run, setRun] = useState<RunSummary>();
  const [evidence, setEvidence] = useState<Evidence>(emptyEvidence);
  const [apiError, setApiError] = useState('');

  const refresh = useCallback(async () => {
    try {
      const [doctorResult, runList] = await Promise.all([getJson<Doctor>('/api/doctor'), getJson<RunSummary[]>('/api/runs')]);
      setDoctor(doctorResult);
      setRuns(runList);
      const id = selectedRun || runList[0]?.run_id;
      if (id) {
        const [summary, evidenceResult] = await Promise.all([getJson<RunSummary>(`/api/runs/${id}`), getJson<Evidence>(`/api/runs/${id}/evidence`)]);
        setRun(summary);
        setEvidence(evidenceResult);
        if (!selectedRun) setSelectedRun(id);
      }
      setApiError('');
    } catch (cause) {
      setApiError(cause instanceof Error ? cause.message : 'Control Room API is unavailable');
    }
  }, [selectedRun]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (!selectedRun) return;
    localStorage.setItem('zerohandoff-run', selectedRun);
    const stream = new EventSource(`/api/runs/${selectedRun}/events`);
    const update = () => void refresh();
    stream.addEventListener('zerohandoff', update);
    stream.addEventListener('terminal', update);
    stream.onerror = () => stream.close();
    return () => stream.close();
  }, [selectedRun, refresh]);

  const artifacts = evidence.artifacts || [];
  const nav = useMemo(() => [
    ['setup', 'Setup', '01'], ['live', 'Live run', '02'], ['evidence', 'Evidence', '03'], ['delivery', 'Delivery', '04'],
  ] as const, []);

  const cancel = async () => {
    if (!run) return;
    await getJson(`/api/runs/${run.run_id}/cancel`, { method: 'POST' });
    await refresh();
  };

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#top" aria-label="ZeroHandoff home"><span>Z</span><div><b>ZEROHANDOFF</b><small>Control Room</small></div></a>
      <nav aria-label="Control Room views">{nav.map(([id, label, number]) => <button className={view === id ? 'selected' : ''} onClick={() => setView(id)} key={id}><span>{number}</span>{label}{id === 'live' && run?.status === 'running' && <i />}</button>)}</nav>
      <div className="sidebar-bottom">
        <span className="kicker">Selected run</span>
        <select aria-label="Selected run" value={selectedRun} onChange={event => setSelectedRun(event.target.value)}>
          <option value="">No run selected</option>{runs.map(item => <option value={item.run_id} key={item.run_id}>{item.run_id}</option>)}
        </select>
        <div className="runtime-line"><Mark ok={Boolean(doctor?.ok)} /><span>Codex runtime</span><small>{doctor?.ok ? 'Ready' : 'Checking'}</small></div>
      </div>
    </aside>
    <main id="top" className="content">
      <header className="topbar"><div><span className="kicker">Autonomous software delivery</span><h1>{view === 'setup' ? 'Configure' : view === 'live' ? 'Orchestrate' : view === 'evidence' ? 'Audit' : 'Deliver'}</h1></div>{run && <div className="top-run"><StatusPill status={run.status} /><span>{run.current_stage || 'INTAKE'}</span></div>}</header>
      {apiError && <p className="api-error" role="alert">{apiError}</p>}
      {view === 'setup' && <SetupView doctor={doctor} run={run} />}
      {view === 'live' && <LiveView run={run} artifacts={artifacts} onCancel={cancel} />}
      {view === 'evidence' && <EvidenceView run={run} evidence={evidence} />}
      {view === 'delivery' && <DeliveryView run={run} />}
    </main>
  </div>;
}
