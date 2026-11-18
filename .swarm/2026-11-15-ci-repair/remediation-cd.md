# Remediation CD CI failure

## Finding

GitHub run `43737906306` (`main`, push, SHA `a6ff56b4baba2f1d4c7839c9335fc8636bc82a52`) failed in `build-push`: `No Dockerfile found for recommendation`. The commit changed `demo-bugs/*`, which matched the workflow's broad push filter, but those service directories have no Dockerfiles. The only repository Dockerfile is `dashboard/Dockerfile`.

## Fix

On automatic pushes, the detect job now emits a notice and writes an empty service output if the selected service has no Dockerfile in either expected build location. `build-push` is gated on a non-empty service; the dependent promotion job already requires a non-empty image. `workflow_dispatch` exits early from detection with the explicitly requested service, so its build still reports a clear missing-Dockerfile error.

## Checks

- Workflow parsed with PyYAML; detect script passes `bash -n`.
- Executed detect logic: a push without a build context outputs `service=` and a skip notice; dispatch outputs the requested service.
- Executed existing build script with dispatch service `checkout`: it still exits with `No Dockerfile found for checkout`.
- `git diff --check -- .github/workflows/remediation-cd.yml` passed.
