# Frontend CI lane

- Worktree: `/home/sakthi/projects/causa`
- Scope: `dashboard/frontend`
- Environment: Node `v26.12.1`; npm `11.17.0`.
- Safety: existing `dashboard/frontend/node_modules` and `dashboard/frontend/dist` were present. Ran all commands in disposable copy `/tmp/causa-frontend-ci.whUteN/frontend`; did not alter the repo frontend. Original and copied `package-lock.json` SHA-256 match (`75498d5265706a09a2135226a901ee67cac2c0858205ce129b62d57cb9c140bc`). Final `git status --short -- dashboard/frontend` was empty.

## Results

- `npm ci` — PASS (exit 0); added 336 packages in 6s. npm emitted deprecation warnings for `whatwg-encoding@3.1.1` and `recharts@2.15.4`.
- `npm audit` — BLOCKED (exit 1); request to `https://registry.npmjs.org/-/npm/v1/security/advisories/bulk` failed with `getaddrinfo EAI_AGAIN registry.npmjs.org`; npm then reported `audit endpoint returned an error`. No vulnerability result was obtained.
- `npm run lint` — PASS (exit 0; `eslint . --max-warnings 0`).
- `npm test` — PASS (exit 0); Vitest `4.1.12`, 7 test files and 10 tests passed; duration 1.01s.
- `npm run build` — PASS (exit 0); Vite `8.3.8`, 2,455 modules transformed, production build completed in 679ms.

No source, lockfile, existing dependency directory, or existing build output was modified.
