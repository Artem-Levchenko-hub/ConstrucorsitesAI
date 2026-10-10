# MAX chat credential intake: public-code false positives

Base: `95d64962f42fc053b2abc1d19887dec9b123e4e0`.

## Reproduced failure

`LABELLED_SECRET_PATTERN` had no identifier boundaries. It matched the suffix
`Token` in public source expressions `requestToken===detailsSeq.current.` and
`const opToken=++operationCounter.current;`. The real detector returned true;
the resolver returned `needs_provider`; ChatPanel stopped before generation
with the provider-selection toast. No actual credential was needed to trigger
the failure. The tests exercise detection, resolution and redaction together.

## Narrow change and security review

Standalone labels now require Unicode letter/number/underscore/dollar identifier
boundaries. Unquoted values are exempted only if the entire value is a supported
environment reference or React `.current` reference, optionally prefixed by a
comparison/increment operator. Quoted literals remain eligible for intake.
Known secret formats remain detected independently, including inside source code.
No blanket code-block exclusion, disabled secret detector or changed submission
path was added. Provider aliases and credential storage are unchanged.

An initial operator-prefix exemption failed independent review: an opaque
base64-like value beginning `++` could bypass intake. Four new synthetic
falsifiers were observed red, then passed after restricting the exemption to
complete references. Independent re-review returned no remaining blocking
findings, with 43 additional synthetic falsifier checks. A pre-existing minor
false positive for identifiers containing combining marks remains outside this
bounded fix; do not claim complete JavaScript parsing.

## Verified locally

- Original regression run: 8 failures / 12 passed, including both reported inputs.
- Final credential/ingress suite: 32 passed; all examples use synthetic values.
- Full Web suite: 114 files / 1016 tests passed. Existing jsdom scrollTo notices
  were emitted; no failing tests.
- Production Web build: exit 0, including Next type validation.
- Focused ESLint and git diff check: clean.
- Independent review: no remaining blocking findings after the P1 was closed.

Counts overlap and are not summed. Exact CI and installed production evidence
must be recorded in the PR delivery receipt, not inferred from local checks.

## Delivery and acceptance boundary

This is a Web intake fix. API, provider operations, generated Coffee source,
database, credentials and bot bindings are unchanged. The coordinating Windows
owner retains serial production delivery through the documented canonical path.
An active Coffee generation prevents restart/activation; wait for terminal and
fresh global idle before the normal backup/drain/verify/end loop. No Linux
production mutation or provider call was performed for this fix.

Production browser acceptance is pending: an ordinary public-code prompt should
reach normal admission without the credential toast; a synthetic recognized
credential should still enter protected intake. Do not dispatch an extra paid
generation merely to test the classifier. This does not establish generated
checkout quality or MAX/MoySklad E2E success.
