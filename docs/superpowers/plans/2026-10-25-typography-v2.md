# Typography v2: delivery plan

Scope: twenty pinned, Cyrillic-capable default families; semantic pairs/triples;
owner choice by free family name, trusted catalogue page or font file; preserve
accepted typography during ordinary edits. Root owns backend/Git/deployment;
font_gallery_html owns web files. Independent reviewer is read-only.

1. Add regression tests for semantic precedence, twenty-family integrity, triples,
   explicit overrides, unchanged legacy request identities and snapshot retention.
2. Extend `services/typography.py` and the pinned Fontsource data: inspect actual
   normal WOFF2 faces, licenses, package integrity and frozen install entries.
   Add display/body/UI/accent/code roles, restrained use of display faces.
3. Add owner/project-scoped immutable `FontAsset` storage and nullable message
   selections/resolved-font metadata (migration 0093). Enforce shared private
   storage quota and cleanup. Resolve free names before paid dispatch; preserve
   accepted pins on replay. Never fetch arbitrary URLs or execute npm packages.
4. Validate uploaded SFNT/WOFF/WOFF2 in a bounded subprocess and OTS; normalize to
   WOFF2, retain license/name metadata. Cap input at 8 MiB, each normalized face
   at 1 MiB. Embed validated faces in separate digest-addressed UTF-8 CSS files
   below the 2 MiB source limit. No private keys/URLs in generated source.
5. Extend prompt identity, message binding and recovery dispatch consistently;
   apply source additions through the fenced bootstrap before the model. The
   existing source remains authoritative without explicit font intent.
6. Web: independent upload/name picker, roles, chips, image/font mixed drop and
   paste, durable queue acknowledgement and retry identity. Preserve image flows.
7. Run focused falsifying tests, full required API/web gates, diff/secret sanity;
   independent Astra review; fix findings; commit intended files and push upstream.
8. Wait for exact pushed CI revision; deploy that SHA via documented production
   compose/deploy-prod.sh; verify services, schema, HTTP/admission and font user
   flow. Record each paid live attempt and its actual result. No provider changes.

Acceptance: Scandinavian beats niche; explicit owner choice beats style; triples
use an accent/code/UI role rather than decorative body copy; ordinary edits keep
fonts; foreign asset IDs fail; damaged/oversize files fail before generation;
uploaded and named fonts render after refresh and remain in source snapshots.
