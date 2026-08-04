## Slide 1: Causa — From Alert to Explainable Action

Causa is built around a simple operational reality: detecting that something is wrong is usually much easier than proving where the failure began. In a distributed Kubernetes application, the loudest alert may come from a service that is only experiencing the consequences of an upstream fault.

The system connects observability, causal reasoning, and guarded action in one incident loop. The promise is concise: see the cascade, find the origin, and act with guardrails. The next slide explains why that connection matters.

## Slide 2: The Operational Gap — Symptoms Arrive Faster Than Answers

When a microservice incident propagates, each telemetry source tells only part of the story. Metrics reveal deviation, traces reveal execution paths, logs provide local context, topology shows relationships, and deployment changes provide possible triggers. Operators still have to assemble those fragments under pressure.

The second problem is causal direction. A downstream victim can produce the largest error volume even though the initiating failure occurred elsewhere. Ranking only the loudest anomaly can therefore prioritize a symptom. Finally, acting on an opaque or uncalibrated answer creates more risk than it removes. Causa addresses these three gaps through one durable evidence pipeline.

## Slide 3: One Evidence Pipeline, From Alert to Decision

Read this architecture from left to right. Alertmanager triggers `causa-ingest`, which defines the incident scope and persists a durable snapshot and outbox. Prometheus, Tempo, and Loki provide the metrics, traces, and logs used to reconstruct the event.

Stage 2 performs anomaly detection, graph construction, and incident isolation. The influence graph then ranks candidate origins and attaches evidence. Postgres preserves durable reports and versions, while the dashboard turns the result into an operator investigation.

The final stage is intentionally gated. A remediation plan must pass policy and approval before a leased worker executes it, observes live SLOs, and handles rollback where the action supports it. The foundation rail is important: every stage shares one durable incident context and audit trail.

## Slide 4: Causal RCA — Rank Origins, Not the Loudest Symptoms

The key idea is to distinguish call direction from failure-influence direction. In a synchronous request, the caller invokes the callee, but a failing callee can propagate damage back toward its callers. Causa reverses those synchronous edges for causal analysis, preserves producer-to-consumer direction for asynchronous flow, and connects infrastructure to hosted services.

Each candidate is scored using three explainable signals. Temporal precedence asks who deviated first. Outward influence asks whether degradation flows from this node toward others. Path consistency checks whether the observed timing agrees with degraded causal paths. Layer arbitration can then promote an infrastructure origin above a hosted victim.

This remains conservative in multi-fault cases. The isolation stages separate disjoint or shared-dependency incidents when the evidence supports it, but abstain when near-simultaneous faults remain ambiguous.

## Slide 5: The Investigation Workspace — Evidence You Can Challenge

The dashboard is designed as an investigation workspace, not merely an answer screen. Operators begin with a prioritized incident inbox and durable history, then open a report that clearly separates the root-cause story, analysis status, warnings, and a conservative confidence measure that is not presented as a probability.

Inside the report, two hypotheses can be compared side by side. The operator can inspect supporting and contradicting evidence, score decomposition, causal paths, unexplained residuals, telemetry coverage, impact, deployment changes, and topology differences. Every evidence item can pivot directly into the matching metrics, logs, traces, topology, or change window.

The final piece is feedback. Operators can accept the result or provide a corrected root service, failure type, and missing evidence. That creates the labeled evidence needed to evaluate and eventually calibrate the system.

## Slide 6: Guarded Remediation — Automation With a Brake Pedal

Causa does not turn an RCA label directly into an unchecked command. It first creates a typed, immutable plan with an exact target, a content fingerprint, risk tier, required approvals, and SLO gates. Depending on the fault class, the proposal may be a configuration patch, rollback, scale or restart action, data-path recovery, or a draft code-fix pull request.

A human approval gate comes before execution. A dedicated worker claims an approved plan using a lease and heartbeat, which prevents stale or competing workers from acting on the same plan. The worker records the lifecycle, observes live SLOs, and either confirms success, fails safely, or initiates supported rollback handling.

The central safety decision is explicit: automatic execution remains blocked until validated calibration and evidence requirements pass. The current default is proposal-only.

## Slide 7: What Is Proven — and What Is Deliberately Gated

The left side shows what has already been exercised in a controlled environment: the full pipeline from Prometheus and Alertmanager through ingestion, RCA, persistence, API, and UI. Deterministic fixtures cover a clear cascade, an infrastructure-rooted incident, disjoint faults, a shared dependency, and an ambiguous case that should abstain.

The right side shows the boundary before autonomy. Production readiness requires a versioned labeled corpus, disjoint training, calibration, and holdout sets, sufficient examples for every automatically remediable action, and published ranking, abstention, calibration, and time-to-diagnosis metrics. It also requires operational controls such as restricted access, dedicated identities, immutable images, recovery drills, and honest rollback-failure reporting.

This is an intentional posture, not a hidden caveat: Causa is suitable for development and controlled evaluation, and remains proposal-only until these gates pass.

## Slide 8: The Outcome — A Closed, Accountable Incident Loop

The final loop brings the system together. Detect uses cross-signal observability. Explain uses an influence-aware graph to rank origins with evidence. Investigate gives the operator one connected workspace to challenge the result. Act uses typed plans, approval, SLO checks, and rollback handling. Learn turns operator feedback and labeled incidents into better evaluation and calibration.

The outcome is a shorter path from alert to accountable action without hiding uncertainty. The practical next step is a controlled pilot: run representative incidents, capture clean baselines and operator-confirmed labels, and measure the system against the readiness criteria before expanding autonomy.
