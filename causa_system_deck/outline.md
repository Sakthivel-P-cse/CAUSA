# Causa — Observability-Driven Root Cause Analysis for Kubernetes

Audience: engineering leaders, SRE/platform teams, and technical evaluators  
Goal: explain the product value, system design, investigation experience, safety model, and current maturity  
Format: 8 slides, 16:9, English

## Slide 1: Causa — From Alert to Explainable Action

- Causa is an observability-driven root-cause analysis and guarded-remediation system for Kubernetes microservices.
- It correlates Prometheus metrics, Tempo traces, Loki logs, topology, and infrastructure context.
- Core promise: turn noisy alerts into ranked, evidence-backed causes and operator-controlled action.
- Positioning line: “See the cascade. Find the origin. Act with guardrails.”
- Layout role: cover; establish the system’s identity and end-to-end scope.
- Visual idea: a clean signal-flow motif moving from telemetry streams into a causal graph and a guarded action.
- Required source images: none.

## Slide 2: The Operational Gap — Symptoms Arrive Faster Than Answers

- Microservice failures cascade across services and infrastructure, so the alerted component is often only a victim.
- Metrics, traces, logs, deployment changes, and topology live in separate investigative surfaces.
- Flat anomaly scores and naive dependency direction can mistake downstream symptoms for the initiating fault.
- High-impact remediation cannot safely depend on opaque confidence or unchecked automation.
- Layout role: problem/context; make the cost and investigative fragmentation tangible.
- Visual idea: a fragmented “war room” feeding one overloaded operator, contrasted with a hidden upstream origin.
- Required source images: none.

## Slide 3: One Evidence Pipeline, From Alert to Decision

- Alertmanager triggers `causa-ingest`, which scopes the incident and persists a durable snapshot/outbox.
- Stage 2 combines anomaly detection, topology, trace execution, and incident isolation.
- The RCA kernel constructs an influence graph and produces ranked root-cause candidates with explanations.
- Postgres preserves incidents and report versions; the dashboard turns results into an investigation workflow.
- Proposed remediation passes through policy, human approval, a leased worker, SLO observation, and rollback handling.
- Layout role: architecture/process; show the complete system boundary and major components.
- Visual idea: a left-to-right architecture pipeline with telemetry sources above, durable state below, and a safety gate before execution.
- Required source images: none.

## Slide 4: Causal RCA — Rank Origins, Not the Loudest Symptoms

- Causa builds four graph views: infrastructure, service dependency, trace execution, and an incident-specific subgraph.
- It orients synchronous calls in the direction of failure influence, preserves asynchronous flow, and maps infrastructure to hosted services.
- Candidate score combines temporal precedence (T), outward influence (D), and path consistency (P).
- Layer arbitration can promote an infrastructure origin above a hosted service and label the service as a victim.
- Multi-fault isolation separates disjoint or shared-dependency incidents and abstains when evidence remains ambiguous.
- Layout role: technical concept explanation; make the ranking logic understandable without code.
- Visual idea: a compact before/after graph showing call direction versus failure-influence direction, plus the T/D/P scoring equation.
- Required source images: none.

## Slide 5: The Investigation Workspace — Evidence You Can Challenge

- The operational overview prioritizes active alerts, degraded services, incident activity, and high-value insights.
- The incident inbox supports search, faceted filtering, confidence thresholds, prioritization, saved views, and durable history.
- A seven-tab investigation view connects the RCA story, competing hypotheses, confidence dimensions, evidence, timeline, topology, and raw detail.
- Every evidence item can pivot back into the relevant metrics, logs, traces, topology, or change window.
- Feedback captures accepted or corrected root causes and missing evidence for future evaluation.
- Layout role: product experience/case walkthrough; show how an operator moves from summary to proof.
- Visual idea: a stylized application workspace with a central incident narrative, hypothesis comparison, and evidence pivots.
- Required source images: none; UI-inspired composition should be generated, not presented as a literal product screenshot.

## Slide 6: Guarded Remediation — Automation With a Brake Pedal

- RCA results become typed, immutable plans with exact targets, fingerprints, risk tiers, required approvals, and SLO gates.
- Plans can propose configuration changes, dependency rollback, scaling/restart actions, data-path recovery, or a draft code-fix PR.
- A human approval gate precedes execution; dedicated workers claim plans using leases and record an auditable lifecycle.
- Live SLO observation determines success, failure, or rollback; stale owners and unsafe targets are rejected.
- Automatic execution remains disabled without a validated calibration artifact and sufficient labeled incidents.
- Layout role: governance/process; communicate safety, reversibility, and operator control.
- Visual idea: a gated loop from “propose” to “approve” to “execute” to “observe,” with rollback returning to a safe state.
- Required source images: none.

## Slide 7: What Is Proven — and What Is Deliberately Gated

- The end-to-end path has been exercised across Prometheus, Alertmanager, ingestion, RCA, Postgres, API, and UI in the OpenTelemetry demo environment.
- Deterministic fixtures cover clear cascades, infrastructure-rooted failures, disjoint dual faults, shared dependencies, and ambiguous incidents.
- Production readiness requires a versioned labeled corpus, disjoint training/calibration/holdout sets, and published ranking and calibration metrics.
- Current limitations include evaluation-quality dependence on clean baselines, representative alert rules, fitted weights, and calibrated confidence.
- Product posture: development and controlled evaluation today; proposal-only until evidence and operational gates pass.
- Layout role: evidence/maturity; balance capability with honest readiness.
- Visual idea: a two-column “Validated now / Required before autonomy” scorecard with a prominent proposal-only status.
- Required source images: none.

## Slide 8: The Outcome — A Closed, Accountable Incident Loop

- Detect with cross-signal observability.
- Explain with influence-aware, ranked causal evidence.
- Investigate through one connected operator workspace.
- Act through typed plans, approval, SLO checks, and rollback.
- Learn from feedback and calibrated evaluation before expanding autonomy.
- Closing message: Causa compresses the path from alert to accountable action without hiding uncertainty.
- Layout role: summary/closing; reinforce the five-stage value loop and invite a controlled pilot.
- Visual idea: a circular Detect → Explain → Investigate → Act → Learn loop around the Causa mark.
- Required source images: none.
