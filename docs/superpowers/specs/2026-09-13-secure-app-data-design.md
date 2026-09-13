# Secure application data

Approved in conversation: implement encryption, complete CRUD, automatic generation integration, authorization, recovery and automated tests. The user will perform manual application acceptance.

## Boundaries

The trusted MAX core owns the managed PostgreSQL connection and encryption keys. Generated code and the browser never receive those keys. Reuse verified MAX sessions and the reserved `/api/omnia` gateway. Each project has independent ciphertext context and keys. Encrypt complete record payloads with authenticated encryption; metadata needed for ownership and pagination is explicit, not advertised as encrypted.

New project PostgreSQL instances must start with the existing controller-owned runtime role, signed actor verification and RLS, rather than handing an unrestricted administrator to the generated application. Existing data is preserved; transition of legacy databases must use the existing protection workflow and cannot infer ownership.

## Managed CRUD

Create, list, read, update and delete records through a trusted API. Server selects identity from MAX authentication. Collection/id validation, bounded requests, pagination, conflict detection, transaction-safe writes and audit events without payloads are mandatory. Missing keys, invalid ciphertext and unauthorized users fail closed. Browser API is typed and documented for generation. Tests exercise crypto tampering, wrong-project keys, CRUD persistence, cross-user denial and concurrent updates.

## Keys and infrastructure

Keys must be independent of auth secrets, versioned, and available after restart and backup restoration. Production enablement requires separately managed key storage; the current host has no verified KMS/Vault or encrypted block device. A user question about the available key service is pending. Do not introduce a plaintext-key fallback or claim disk encryption from application encryption. Do not convert the live root disk or delete legacy database volumes.

## Delivery

Primary owns integration, Git and production. Automated verification and service health are in scope; manual business-app acceptance is reserved to the user. Production uses `/opt/omnia`, compose project `full`, `apps/llm-gateway/deploy/full/docker-compose.yml`, and `omnia-orchestrator.service`. Preserve unrelated local/server changes. Report any unmet key/storage/deployment prerequisites explicitly.
