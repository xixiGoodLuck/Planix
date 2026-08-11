# Planix Learning engineering guide

Planix has one production domain: Learning. The backend runtime lives in `Backend/backend/app/learning`, the frontend workspace lives in `Frontend/src/features/learning`, and the only model task is `learning_semantic`.

Keep semantic work in the existing Learning generators and mapping layer. Keep IDs, versions, lineage, timestamp ownership, validation, coverage strength, and quality pass/fail deterministic. Never create exact video ranges from metadata or model output.

Production requires PostgreSQL 17 and explicit provider configuration. AI secrets stay in the platform SecretStore. Learning persistence, checkpoints, recovery, resume, and progress events remain isolated from AI Settings metadata.

The user-facing routes are `#/learning` and `#/settings`; the default is Learning. The backend product APIs are `/api/learning/*`, `/api/ai/*`, and `/health`.
