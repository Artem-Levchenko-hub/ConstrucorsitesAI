# Public guide screenshot redaction

**Goal:** Hide actual account, project, bot and business data in every public guide image while preserving the genuine interface, controls and existing arrows.

**Approach:** Permanently flatten opaque masks into lossless PNG assets under a fresh `2026-10-30-redacted` path. Never rely on CSS, transparent layers or reversible blur. Retain platform names and generic control labels; hide project names, bot identities/avatars, app URLs, entered business content and account usage values. Remove the original JPEG directory from the current public tree. Git history is not rewritten.

**Scope:** The guide manifest, image assets, privacy caption, asset contract test and a reusable deterministic PowerShell redaction recipe. Private inputs and raw captures remain outside the public tree and are not newly committed.

- [x] Add a failing asset contract for lossless redacted PNGs, explicit privacy captions and absence of the raw public directory.
- [x] Flatten 75 opaque masks into all 19 reviewed captures, replace public paths and remove the original public files. Dimensions and opaque pixels verified; EXIF absent.
- [x] Review all resulting images and guide text/URLs; focused tests 9/9, typecheck and lint exit0. Independent Sol/max review found background business text and a duplicated action in a caption; both corrected.
- [ ] Commit, push, canonically deploy web only, verify health, all new images and old URL removal; capture the live public guide.

**Acceptance:** 19 real screenshots remain usable; full-size links open sanitized files. Old original URLs return 404. No account/project/bot data remains in current public assets or guide text. No AI generation, SMS, payments or business-data changes are needed.
