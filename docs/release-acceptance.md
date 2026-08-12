# Planix Learning Release Candidate Acceptance Record

This record is generated from the Phase 31 verification run. It stores only safe runtime metadata; prompts, transcript bodies, credentials, authorization headers, and database connection strings are excluded.

## Baseline

- Phase 30 commit: `a84b1b692a1447fe668bda7c69a3cef91c8302aa`
- Application/package/Tauri/Cargo version: `1.1.4`
- PostgreSQL: `17.10`
- Alembic: `20260812_02 (head)`
- Python: `3.13.2`
- Node: `24.18.0`
- Rust: `1.97.1`

## Controlled performance sample

Three controlled production-contract runs on PostgreSQL 17 produced these observed timings in milliseconds:

| Stage | Min | Median | Max |
| --- | ---: | ---: | ---: |
| Scope analysis | 13 | 17 | 24 |
| Knowledge generation | 23 | 24 | 34 |
| Evidence generation | 15 | 17 | 20 |
| Gap completion | 5 | 6 | 6 |
| Selection | 17 | 19 | 24 |
| Quality | 11 | 13 | 14 |
| Total | 159 | 182 | 221 |

Per run: 4 semantic model calls, 1 metadata-provider call, and 1 transcript lookup. These controlled-adapter results validate measurement shape and regression behavior; they are not an external-provider SLA.

## Version recommendation

Keep `1.1.4` during Phase 31 because release acceptance is not yet complete and the repository requires version changes only after all acceptance items pass. Once the remaining installer and screenshot checks pass, use the repository's existing tag format and cut `v1.1.5-rc.1` (patch-level RC): Phase 31 hardens recovery, CI, documentation, and release UX without adding a new product capability or architecture.

## Open acceptance evidence

- Screenshot set: 7 of 9 verified real-page captures. `waiting_evidence` and same-run resume captures are still missing; no static or fabricated replacement was used.
- Desktop: the release executable compiled successfully, but Windows installer bundling stopped while downloading the NSIS helper (`Peer disconnected`), so a fresh installed smoke test is not verified.
- Dependency audit: production frontend and desktop packages report zero vulnerabilities. The frontend development/test graph reports 9 advisories (3 moderate, 5 high, 1 critical); the Vitest remediation is a major-version update and remains a separately scheduled toolchain upgrade.
