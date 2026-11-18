# Frontend CI audit repair

`dashboard/frontend/package-lock.json` was refreshed within the declared package ranges; `package.json` is unchanged. The repaired tree resolves current advisories, including Vitest/@vitest/mocker, brace-expansion, minimatch, nanoid, React Router, PostCSS, source-map-js, Vite, and zod.

Checks after `npm ci` from the lockfile:
- `npm audit --audit-level=high`: 0 vulnerabilities
- `npm run lint`: passed
- `npm test`: 7 files, 10 tests passed
- `npm run build`: passed

The lockfile refresh updated 346 lines and removed 267; no manifest ranges changed.
