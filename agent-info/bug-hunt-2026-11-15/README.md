# Read-only swarm bug hunt

Baseline: current checkout including existing untracked source. Do not modify application files.

Each reviewer owns its report file in this directory. Root owns shared coordination JSON to avoid write races; send status changes to root. Reports must distinguish verified defects, probable defects, design risks and rejected candidates. Include trigger, source anchors, causal path, minimal fix direction, checks and blind spots. No live infrastructure mutations or remediation execution.
