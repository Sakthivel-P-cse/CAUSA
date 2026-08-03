#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str]) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {p.stderr or p.stdout}")
    return p.stdout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("service")
    ap.add_argument("--context", default="kind-gcp-demo-test")
    ap.add_argument("--namespace", default="otel-demo")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    dep_raw = run(["kubectl", "--context", args.context, "-n", args.namespace, "get", "deploy", args.service, "-o", "json"])
    dep = json.loads(dep_raw)
    tmpl = dep["spec"]["template"]
    selector = dep["spec"]["selector"]
    replicas = dep["spec"].get("replicas", 1)

    rollout = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Rollout",
        "metadata": {
            "name": args.service,
            "namespace": args.namespace,
            "labels": (dep.get("metadata", {}).get("labels") or {}),
        },
        "spec": {
            "replicas": replicas,
            "revisionHistoryLimit": 3,
            "selector": selector,
            "template": tmpl,
            "strategy": {
                "canary": {
                    "maxSurge": "25%",
                    "maxUnavailable": 0,
                    "steps": [
                        {"setWeight": 10},
                        {"pause": {"duration": 20}},
                        {
                            "analysis": {
                                "templates": [{"templateName": "causa-prometheus-success"}],
                                "args": [{"name": "service-name", "value": args.service}],
                            }
                        },
                        {"setWeight": 50},
                        {"pause": {"duration": 30}},
                        {"setWeight": 100},
                    ],
                }
            },
        },
    }
    text = json.dumps(rollout, indent=2)
    out = Path(f"/tmp/{args.service}-rollout.json")
    out.write_text(text)
    print(out)
    if args.apply:
        run(["kubectl", "--context", args.context, "apply", "-f", str(out)])
        run(["kubectl", "--context", args.context, "-n", args.namespace, "scale", f"deploy/{args.service}", "--replicas=0"])
        print(f"applied rollout and scaled deploy/{args.service} to 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
