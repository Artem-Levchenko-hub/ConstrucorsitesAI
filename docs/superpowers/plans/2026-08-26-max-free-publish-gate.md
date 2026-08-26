# MAX Free Preview and Paid Publication — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Обновить автономный MAX CJM так, чтобы Free давал полезную генерацию и приватное превью, но не публичный запуск; подтверждённый Pro/Business открывал публикацию либо на Omnia-hosting, либо на VPS пользователя с разным поведением после окончания периода.

**Architecture:** Один self-contained HTML остаётся локальным конечным автоматом без сетевых вызовов. Тариф, подтверждение платежа, намерение публикации, способ хостинга и состояние запущенной версии хранятся раздельно; все UI-действия вызывают одни и те же проверяемые переходы, поэтому скрытая кнопка, карта CJM, клавиатура и прямой dispatch не обходят тарифный шлюз. Один Sol-владелец последовательно меняет артефакт, независимый reviewer проверяет требования и регрессии, Luna выполняет только доставку репозиторных доказательств.

**Tech Stack:** HTML5, CSS3, vanilla JavaScript, localStorage v3, PowerShell structural smoke, Node `vm` behavior smoke, in-app Browser verification.

**Spec:** `docs/superpowers/specs/2026-08-26-max-free-publish-gate-design.md`

## Global Constraints

- Modify only `C:\Users\79133\Downloads\omnia-max-cjm-customer-flow.html` as the interactive artifact.
- Preserve `C:\Users\79133\Downloads\omnia-max-cjm-customer-flow (1).html` byte-for-byte as the baseline copy. Its baseline SHA-256 is `8F73B3E6CEBB296DBB20E1CE44543C772EA2DE8DB89B3BEDCE848C1BA469BC13`, size `192875` bytes.
- Do not change production code under `apps/`; this iteration is an autonomous UX demonstration only.
- Keep the existing 16 screens, seven phases, warm Omnia visual system, workspace shell, live preview, form values, saved versions, error-recovery paths, keyboard behavior, and local-only operation.
- Free must never assign `paid=true` or `paymentStatus="succeeded"`. A paid plan is usable for publication only after a simulated server-confirmed success.
- Do not say that the Free user does not own the application. Say what remains saved, what Free permits, and what production operation requires.
- Omnia-hosted publication pauses after the paid period; project, configuration, versions, data represented in the prototype, and address remain saved.
- A successfully installed VPS copy remains online and user-controlled after the paid period; future managed changes and releases through Omnia become locked.
- Never collect or transmit real payment or VPS credentials. The VPS verification is explicitly simulated and local.
- Preserve user-owned repository changes in `AGENTS.md`, `.codex-migration-backups/`, `.codex-tmp/`, and `tmp/`.
- Write all scratch tests and evidence only under ignored `.superpowers/sdd/2026-08-26-max-free-publish-gate/`.

---

### Task 1: Characterize the artifact and make the publication contract executable

**Files:**
- Modify: `C:\Users\79133\Downloads\omnia-max-cjm-customer-flow.html`
- Create (scratch, ignored): `.superpowers/sdd/2026-08-26-max-free-publish-gate/smoke.ps1`
- Create (scratch, ignored): `.superpowers/sdd/2026-08-26-max-free-publish-gate/behavior-smoke.mjs`
- Create (scratch, ignored): `.superpowers/sdd/2026-08-26-max-free-publish-gate/report.md`
- Verify unchanged: `C:\Users\79133\Downloads\omnia-max-cjm-customer-flow (1).html`

**Interfaces:**
- Adds state fields `freeActivated`, `publicationIntent`, `hostingMode`, `hostingTermsAccepted`, `vpsVerified`, `vpsResponsibilityAccepted`, `vpsLabel`, `hostedStatus`, and `subscriptionExpired`.
- Adds transition helpers `isPaidPlan()`, `canUsePreview()`, `canPublish()`, `canCompletePublication()`, `activateFree()`, `beginPublicationUpgrade()`, `confirmPaidAccess()`, `selectHostingMode(mode)`, `completePublication()`, and `expirePaidPeriod()`.
- Replaces the unsafe publication meaning of `canOpenPaidProduct()`; it may be removed or reduced to a non-publication navigation helper, but it must not return true merely because `plan === "free"`.

- [ ] **Step 1: Record the immutable baseline**

Run:

```powershell
$target = 'C:\Users\79133\Downloads\omnia-max-cjm-customer-flow.html'
$reference = 'C:\Users\79133\Downloads\omnia-max-cjm-customer-flow (1).html'
Get-FileHash -Algorithm SHA256 -LiteralPath $target, $reference
Get-Item -LiteralPath $target, $reference | Select-Object FullName, Length
```

Expected before editing: both files are `192875` bytes with SHA-256 `8F73B3E6CEBB296DBB20E1CE44543C772EA2DE8DB89B3BEDCE848C1BA469BC13`. Append the output to `report.md`.

- [ ] **Step 2: Write structural and behavior smoke checks before implementation**

`smoke.ps1` must load the target, assert the reference hash, reject `fetch(`, `XMLHttpRequest`, `WebSocket(`, and external `<script src=...>`/`<link href=...>`, then assert the new storage key, state fields, helper names, customer copy, and action names.

The PowerShell test invokes `node behavior-smoke.mjs`. The Node harness must:

1. extract the final inline script;
2. inject an in-memory export immediately before the bootstrap `render()` call;
3. run the script in `node:vm` with minimal `window`, `document`, `localStorage`, timer, clipboard, and root-element stubs;
4. expose the local `state`, `defaults`, and transition helpers only inside the test process—do not add a debug API to the delivered HTML.

Core pre-implementation assertions:

```js
assert.equal(ctx.isPaidPlan(), false);
ctx.activateFree();
assert.equal(ctx.state.plan, 'free');
assert.equal(ctx.state.freeActivated, true);
assert.equal(ctx.state.paid, false);
assert.equal(ctx.state.paymentStatus, 'idle');
assert.equal(ctx.canUsePreview(), true);
assert.equal(ctx.canPublish(), false);
assert.equal(ctx.completePublication(), false);
assert.equal(ctx.state.published, false);

ctx.beginPublicationUpgrade();
assert.equal(ctx.state.publicationIntent, true);
assert.equal(ctx.state.publishName, 'Кофе рядом');
assert.equal(ctx.state.publishSlug, 'coffee-near');
```

- [ ] **Step 3: Run the new checks and record RED**

Run:

```powershell
& '.\.superpowers\sdd\2026-08-26-max-free-publish-gate\smoke.ps1'
```

Expected: exit `1` because v3 state fields/helpers and the new copy do not exist. Record the exact first failure in `report.md`.

- [ ] **Step 4: Implement the v3 state and authoritative guards**

Bump `STORAGE_KEY` to `omnia-max-cjm-customer-flow-v3`. Keep the existing nested merge for `user` and `brief`, and normalize every new boolean/enum on load. A v2 Free state must not be imported as paid; using a new key is the primary migration barrier.

Use these invariants:

```js
function isPaidPlan() {
  return state.plan === 'pro' || state.plan === 'business';
}

function canUsePreview() {
  return (state.plan === 'free' && state.freeActivated) || canPublish();
}

function canPublish() {
  return isPaidPlan() && state.paid === true &&
    state.paymentStatus === 'succeeded' && !state.subscriptionExpired;
}

function canCompletePublication() {
  if (!canPublish()) return false;
  if (state.hostingMode === 'omnia') return state.hostingTermsAccepted === true;
  return state.hostingMode === 'vps' && state.vpsVerified === true &&
    state.vpsResponsibilityAccepted === true;
}
```

`activateFree()` sets `freeActivated=true`, `paid=false`, `paymentStatus="idle"`, disables renewal/method saving, and leaves project fields untouched. `confirmPaidAccess()` is the only helper that makes paid publication available, and only after the simulated confirmed-success branch.

- [ ] **Step 5: Make navigation use the same guards**

Change `go(step)` so it never silently assigns Pro when no plan exists. Steps 8–12 require `canUsePreview()`; step 13 is always renderable because it contains the honest locked publication state; step 14 requires `state.published`; account navigation preserves the current project.

The map, dock, sidebar, Next button, Enter/Space activation, and direct `publish` action must all converge on `canPublish()`/`completePublication()`. A hidden or disabled button is not the security boundary.

- [ ] **Step 6: Run Task 1 checks to GREEN**

Expected output includes:

```text
PASS structure, storage and zero-network contract
PASS Free preview and paid publication guards
```

Append exact output and the new target hash to `report.md`.

---

### Task 2: Preserve the ready Free project through upgrade and payment recovery

**Files:**
- Modify: `C:\Users\79133\Downloads\omnia-max-cjm-customer-flow.html`
- Modify: `.superpowers/sdd/2026-08-26-max-free-publish-gate/behavior-smoke.mjs`
- Modify: `.superpowers/sdd/2026-08-26-max-free-publish-gate/report.md`

**Interfaces:**
- `beginPublicationUpgrade()` sets `publicationIntent=true` and changes no brief, generated, version, publish-name, slug, or visibility fields.
- `paidDestination()` returns step `13` for a ready publication intent and step `7` for an ordinary paid activation.
- `confirmPaidAccess()` centralizes confirmed-payment state and never runs for pending/failed/cancelled/expired payment.

- [ ] **Step 1: Extend behavior smoke with failing continuity assertions**

Set a distinct generated project (`brief.product`, `generated=true`, `version=3`, `publishName`, `publishSlug`, `visibility`), start upgrade, then exercise payment statuses.

Assert:

```js
for (const status of ['pending', 'processing', 'failed', 'cancelled', 'expired']) {
  ctx.state.plan = 'pro';
  ctx.state.paymentStatus = status;
  ctx.state.paid = false;
  assert.equal(ctx.canPublish(), false, status);
}

ctx.state.plan = 'pro';
ctx.confirmPaidAccess();
assert.equal(ctx.state.paymentStatus, 'succeeded');
assert.equal(ctx.state.paid, true);
assert.equal(ctx.paidDestination(), 13);
assert.deepEqual(projectSnapshot(ctx.state), beforeUpgrade);
```

Also assert that selecting Pro/Business during this upgrade resets only payment/subscription fields and does not reset the project or `publicationIntent`.

- [ ] **Step 2: Run Task 2 checks and record RED**

Expected: the old return path goes to activation/brief instead of publication, or the new helpers are missing.

- [ ] **Step 3: Implement honest Free and upgrade copy**

Update the Free plan card to say `Первая генерация и приватное превью` and `Без публичной публикации`. Update Free activation and activated screens to explain that the project and draft are saved and public launch is available on Pro/Business.

At Free publication, render:

- visible completed-draft/version summary;
- saved name, slug, and visibility;
- the distinction between private preview and public operation;
- dominant `Перейти на Pro и опубликовать` action using `publication-upgrade`;
- secondary return to preview/edit without data loss.

- [ ] **Step 4: Implement return-to-publication routing**

Replace hardcoded success CTAs with `continue-after-payment`. Payment provider return remains locked until `paymentStatus === "succeeded"`; only then call `confirmPaidAccess()` and route to `paidDestination()`. Retry, method change, failure, cancellation, expiry, and pending states keep `publicationIntent` and all project fields.

- [ ] **Step 5: Run Task 1–2 checks to GREEN**

Expected output adds:

```text
PASS upgrade continuity and confirmed-payment return
PASS pending and failed payments stay locked
```

---

### Task 3: Add the two paid publication modes and their lifecycle

**Files:**
- Modify: `C:\Users\79133\Downloads\omnia-max-cjm-customer-flow.html`
- Modify: `.superpowers/sdd/2026-08-26-max-free-publish-gate/behavior-smoke.mjs`
- Modify: `.superpowers/sdd/2026-08-26-max-free-publish-gate/smoke.ps1`
- Modify: `.superpowers/sdd/2026-08-26-max-free-publish-gate/report.md`

**Interfaces:**
- `selectHostingMode('omnia'|'vps')` preserves plan/project/payment state, keeps VPS input/verification data, and invalidates only the consequence confirmations.
- `completePublication()` returns `false` without paid entitlement or mode-specific confirmation; on success it sets `published=true` plus `hostedStatus="omnia-active"` or `hostedStatus="vps-online"`.
- `publicationUrl()` returns the Omnia demo address for Omnia-hosting and the locally entered demo domain/server label for VPS.
- `expirePaidPeriod()` revokes `canPublish()`; it changes Omnia to `omnia-paused` but leaves VPS as `vps-online`.

- [ ] **Step 1: Add failing mode and lifecycle assertions**

Use a confirmed Pro state and assert both branches:

```js
ctx.selectHostingMode('omnia');
assert.equal(ctx.completePublication(), false);
ctx.state.hostingTermsAccepted = true;
assert.equal(ctx.completePublication(), true);
assert.equal(ctx.state.hostedStatus, 'omnia-active');
ctx.expirePaidPeriod();
assert.equal(ctx.canPublish(), false);
assert.equal(ctx.state.hostedStatus, 'omnia-paused');
assert.equal(ctx.state.published, true);

ctx.resetPublicationForTest();
ctx.selectHostingMode('vps');
ctx.state.vpsVerified = true;
ctx.state.vpsResponsibilityAccepted = true;
assert.equal(ctx.completePublication(), true);
assert.equal(ctx.state.hostedStatus, 'vps-online');
ctx.expirePaidPeriod();
assert.equal(ctx.canPublish(), false);
assert.equal(ctx.state.hostedStatus, 'vps-online');
```

`resetPublicationForTest()` is a harness-only operation assembled through the injected state export, not a production function.

- [ ] **Step 2: Run Task 3 checks and record RED**

Expected: hosting selectors/transitions and lifecycle distinctions are missing.

- [ ] **Step 3: Build the paid publication chooser**

In `renderPublish()` branch on `canPublish()`:

- render two keyboard-operable cards with `aria-pressed`: `На хостинге Omnia` and `На своём VPS`;
- recommend Omnia as the shortest launch path without precluding VPS;
- Omnia branch shows the `omnia.app/<slug>` address, managed HTTPS, and a required checkbox that the app pauses after the paid period;
- VPS branch asks only for a harmless demo server/domain label, runs a local `vps-verify` state, and requires responsibility confirmation; never ask for or transmit a real password/private key;
- final CTA text and summary must match the selected mode;
- changing mode preserves the project and paid state and clears only consequence confirmation.

Add restrained CSS for the choice cards, locked state, mode-specific status chips, and responsive stacking. Reuse current tokens and 44 px minimum controls.

- [ ] **Step 4: Enforce both final actions in the dispatcher**

Both `publish` and `vps-install` must call `completePublication()`. When Free or payment is not confirmed, remain on step 13 and show the upgrade explanation. When fields/privacy/mode confirmation are incomplete, focus or explain the exact missing item. A successful transition goes to step 14.

- [ ] **Step 5: Make success and account screens mode-specific**

`renderPublished()` must show:

- Omnia: stable Omnia URL, managed status, saved version, and dependency on the paid period;
- VPS: user-controlled URL/server label, independent running copy, and responsibility for infrastructure/backups/domain.

`renderAccount()` and cancellation copy must show:

- Omnia: active until period end, then paused; project/version/address saved; restore CTA returns through paid confirmation;
- VPS: installed copy stays online; managed generation, updates, and another automated release through Omnia become locked after period end.

Add an explicit local demonstration action `simulate-period-end` only where its purpose is clear. It calls `expirePaidPeriod()` and never deletes project fields. A paused Omnia app can be restored after a new confirmed paid period by returning to publication; a VPS app never receives a remote-disable action.

- [ ] **Step 6: Run all behavior checks to GREEN**

Expected output adds:

```text
PASS Omnia-hosting confirmation, publication and pause
PASS VPS verification, installation and independent operation
PASS project state survives mode changes and period expiry
PASS all MAX paid-publication smoke checks
```

Record the output and target/reference hashes in `report.md`.

---

### Task 4: Browser verification and independent review

**Files:**
- Verify: `C:\Users\79133\Downloads\omnia-max-cjm-customer-flow.html`
- Modify: `.superpowers/sdd/2026-08-26-max-free-publish-gate/report.md`
- Create (scratch, ignored): `.superpowers/sdd/2026-08-26-max-free-publish-gate/review-package.md`

**Interfaces:**
- Consumes the final artifact and smoke suite.
- Produces browser evidence for all gates and a reviewer verdict with no unresolved Critical/Important findings.

- [ ] **Step 1: Serve only the target artifact locally**

Start a localhost server bound to `127.0.0.1`, restricted to the target filename. Confirm the page performs zero requests beyond the document itself.

- [ ] **Step 2: Exercise the Free and upgrade journey**

At 1440×900, complete registration → Free activation → brief → generation → preview → publication. Confirm the generated result remains visible, public launch is locked, direct map/sidebar/keyboard entry renders the same lock, and `localStorage` contains neither paid nor succeeded Free state.

From that locked screen, choose Pro, exercise one pending/failed recovery, finish server-confirmed payment, and confirm return to the same name, slug, visibility, version, and publication screen.

- [ ] **Step 3: Exercise Omnia-hosting lifecycle**

Confirm publication cannot proceed before the dependency checkbox. Publish after confirmation, inspect the Omnia-specific success screen, cancel renewal, simulate period end, and verify the public state is paused while the project/version/address remain. Restore paid access and verify the app can be brought online again.

- [ ] **Step 4: Exercise VPS lifecycle**

Switch mode, confirm the prior mode checkbox is invalidated but project/payment fields survive, verify the simulated server, accept responsibility, and install. Confirm success/account copy says the copy is controlled by the user. Cancel/expire the subscription and verify the VPS copy remains online while managed update/publication controls are locked.

- [ ] **Step 5: Check responsive and accessible operation**

Repeat representative locked, paid chooser, Omnia success/paused, and VPS success/expired states at 1024×768 and 390×844. Check keyboard-only navigation, visible focus, Enter/Space activation, 200% text zoom, reduced motion, no positive `tabindex`, no horizontal overflow, no clipped primary action, and zero console exceptions.

- [ ] **Step 6: Request independent review**

Give the reviewer the spec, this plan, artifact, baseline and final hashes, smoke outputs, and browser evidence. Require exact line evidence for Free semantics, direct-action guard, payment confirmation, state preservation, both hosting branches, period expiry, copy accuracy, accessibility, responsive layout, and zero network behavior.

- [ ] **Step 7: Fix every Critical/Important finding test-first**

For every valid finding, first extend the structural/behavior/browser check to reproduce it, then apply the smallest correction and rerun all affected checks. Return the corrected artifact to the same reviewer until no Critical/Important findings remain.

- [ ] **Step 8: Stop local servers and finalize evidence**

Record the final SHA-256, byte size, reference hash, complete smoke output, browser matrices, console/network result, and reviewer verdict in `report.md`.

---

### Task 5: Live report and delivery

**Files:**
- Modify: `otchet/data.json`
- Verify: `docs/superpowers/specs/2026-08-26-max-free-publish-gate-design.md`
- Verify: `docs/superpowers/plans/2026-08-26-max-free-publish-gate.md`
- External deliverable: `C:\Users\79133\Downloads\omnia-max-cjm-customer-flow.html`

**Interfaces:**
- Consumes final implementation, review, and verification evidence.
- Produces a truthful H118 testing record, completed V8 CJM step, pushed revision, and published `/otchet` data.

- [ ] **Step 1: Update H118 only after verified implementation**

Move H118 from `open` to `testing`, keep `score: null` until a real-user product outcome is measured, and replace the placeholder evidence with final file hash/size, exact state paths, smoke/browser results, and reviewer verdict. Explain the demonstrated product impact without claiming measured conversion.

- [ ] **Step 2: Complete the V8 implementation step**

Set `CJM: Free preview → платная публикация → разное завершение тарифа для Omnia-hosting и собственного VPS` to `true` only after Tasks 1–4 are green. Increment `meta.version`, keep `meta.updated` at the current date, and update the existing 26.08 owner action from “HTML пока не изменён” to the verified outcome.

- [ ] **Step 3: Verify data, artifact, and diff freshness**

Run fresh:

```powershell
python -m json.tool otchet/data.json > $null
& '.\.superpowers\sdd\2026-08-26-max-free-publish-gate\smoke.ps1'
Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\Users\79133\Downloads\omnia-max-cjm-customer-flow.html','C:\Users\79133\Downloads\omnia-max-cjm-customer-flow (1).html'
git diff --check
git diff -- docs/superpowers/plans/2026-08-26-max-free-publish-gate.md otchet/data.json
git status --short --branch
```

Expected: valid JSON, all smoke checks green, reference hash unchanged, no whitespace errors, and no unrelated path staged.

- [ ] **Step 4: Commit and push only repository evidence**

Delegate to `luna_delivery`. Stage only the intended plan/report files for the current delivery slice, commit on `main` with the required co-author trailer, and push to `origin/main`. Never stage the external HTML or scratch evidence because they are outside the repository/ignored.

- [ ] **Step 5: Publish and verify the static live report**

Fast-forward `/opt/omnia` with `git fetch && git merge --ff-only origin/main`, preserve unrelated dirty production files, copy `otchet/data.json` to `/var/www/otchet/data.json`, ensure the public copy is readable, and verify `https://constructor.lead-generator.ru/otchet/` plus its `data.json` return HTTP 200 and contain the new version/H118 evidence.

- [ ] **Step 6: Final handoff**

Report the clickable external artifact, final SHA-256 and size, behavioral/browser/review evidence, repository revision, push/deploy health, unchanged baseline hash, and the explicit fact that production Omnia runtime code was not changed.
