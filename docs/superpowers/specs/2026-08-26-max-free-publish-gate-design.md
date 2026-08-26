# MAX Free Preview and Paid Publication — Design Specification

## 1. Purpose

Update the standalone MAX customer-journey prototype so Free demonstrates the value of generation without silently granting production publication. The user keeps the brief, generated draft, preview, and saved versions, while public operation follows the selected hosting model.

The design separates three concepts that must not be presented as one:

- project ownership: the user's brief, materials, configuration, and saved draft;
- development access: generation, editing, and automated release actions available through Omnia;
- application operation: an active public instance on Omnia hosting or on infrastructure controlled by the user.

## 2. Scope and Deliverable

The implementation target is the existing autonomous artifact:

`C:\Users\79133\Downloads\omnia-max-cjm-customer-flow.html`

The artifact remains a self-contained local demonstration with embedded HTML, CSS, and JavaScript. It must not make network requests, create accounts, accept a real contract, charge money, deploy software, or change the production Omnia application.

The current visual direction and real-site shell remain intact. This change is primarily a journey, state, copy, and publication-screen update.

## 3. Product Decision

Use a hybrid publication model.

### 3.1 Free

Free includes:

- the first generation;
- an interactive private preview;
- the saved project and its current version;
- a clear path back to the project after an interrupted upgrade.

Free does not include:

- a public production URL;
- publication on Omnia hosting;
- automated installation on the user's VPS;
- activation of the public MAX application.

### 3.2 Paid publication on Omnia hosting

An active Pro or Business period allows the user to publish on Omnia hosting. The application remains public through the paid period. If the period ends, the public instance is paused, while the project, configuration, data represented by the prototype, versions, and address reservation remain saved. Restoring a paid plan can bring the application online again.

The interface must explain this consequence before publication and again in subscription management.

### 3.3 Paid installation on the user's VPS

An active Pro or Business period allows Omnia to install the current version on a verified VPS selected by the user. After a successful installation, that running copy is controlled by the user and is not paused or deleted by Omnia when the subscription ends.

After the paid period ends:

- the installed version keeps running on the user's VPS;
- the user remains responsible for the server, domain, availability, backups, and access;
- new generation, managed changes, and another automated release through Omnia require an active paid plan;
- the prototype must not imply that Omnia can remotely disable the already installed copy.

## 4. Customer Language

Do not say that a Free user "does not own the application." That phrase combines intellectual-property language with service access and creates unnecessary distrust.

Use concrete language instead:

- "Проект и черновик сохранены в вашем аккаунте."
- "Free позволяет проверить приложение в приватном превью."
- "Публичный запуск доступен на Pro и Business."
- "На хостинге Omnia приложение работает, пока активен платный тариф."
- "Версия на вашем VPS остаётся под вашим управлением после установки."

The prototype must not promise absolute exclusivity, automatic intellectual-property protection, or legal ownership of third-party libraries and generated materials.

## 5. Journey Design

### 5.1 Free happy path

1. The user registers, verifies email, selects Free, and activates it without a card.
2. Free activation opens the brief, generation, and live preview exactly as a useful product trial.
3. The user can inspect the generated application and reach the publication stage without losing the result.
4. The publication stage renders a completed-draft summary and a locked production card instead of an active publish button.
5. The dominant action is `Выбрать способ запуска` or `Перейти на Pro и опубликовать`, not an error toast.

Direct navigation from the CJM map to publication must render the same honest locked state. It must not invent a paid status or skip the gate.

### 5.2 Upgrade from a ready Free project

1. The user starts upgrade from the locked publication screen.
2. The project stores a publication intent and keeps the current brief, generated state, version, publication name, slug, and visibility.
3. The user compares Pro and Business; Free is shown as the current preview plan and cannot satisfy publication.
4. The ordinary order, offer, renewal, provider, and server-confirmation demonstration remains available.
5. A return from the payment provider does not grant access by itself. Only the simulated server-confirmed `succeeded` state activates paid publication.
6. Successful activation returns to the same project's publication screen, not to a new brief or generation.
7. A failed, cancelled, expired, or pending payment keeps the project and publication intent intact and offers retry, another method, or return to preview.

### 5.3 Paid publication choice

After confirmed Pro or Business activation, the publication screen presents two explicit choices.

#### Omnia hosting

- Recommended for the shortest launch path.
- Shows the stable Omnia address, managed HTTPS, and subscription dependency.
- Requires confirmation that the user understands the app is paused after the paid period ends.
- The simulated publish action proceeds to the existing success screen.

#### Own VPS

- Shows that the server belongs to and is operated by the user.
- Demonstrates a short verification state for IP/SSH/domain without collecting or transmitting real credentials.
- Requires confirmation of operational responsibility.
- The simulated install action proceeds to a VPS-specific success state that says the installed copy remains under the user's control.

Only one hosting choice is active at a time. Changing the choice updates the summary and consequence copy before the final action.

### 5.4 Post-publication and subscription management

The success and account screens adapt to `hostingMode`:

- Omnia hosting: show the Omnia URL, managed status, paid-period dependency, and the consequence of cancellation.
- Own VPS: show the user-controlled URL/server label, independent running status, and responsibility for infrastructure.

Cancellation language must distinguish the branches:

- Omnia hosting: the app runs until the current period ends, then pauses; project data and versions remain.
- Own VPS: the installed version keeps running; Omnia generation and automated future releases stop when paid access ends.

## 6. State Model

The prototype needs explicit state instead of treating Free as paid.

Required concepts:

- `plan`: `free`, `pro`, or `business`;
- `paymentStatus`: existing provider lifecycle;
- `paid`: true only after a paid Pro/Business activation is server-confirmed;
- `publicationIntent`: whether the user entered checkout from a ready project;
- `hostingMode`: `omnia` or `vps`;
- `hostingTermsAccepted`: consequence confirmation for the selected hosting mode;
- `published`: whether the current demonstration reached a successful release;
- `hostedStatus`: enough state to distinguish active Omnia hosting, paused Omnia hosting, and a user-controlled VPS installation.

Derived guards:

- `canUsePreview()` permits the activated Free branch and confirmed paid branches.
- `canPublish()` requires Pro or Business plus confirmed paid activation.
- `canManageOmniaHosting()` additionally depends on `hostingMode === "omnia"`.

Free activation must never set `paid=true` or `paymentStatus="succeeded"`. Direct action handling must enforce `canPublish()` even when the UI button is hidden or disabled.

Bump the local-storage schema key or provide normalization so saved state from the previous prototype cannot carry the old Free-as-paid behavior into the new journey.

## 7. Screen and Navigation Changes

The artifact may retain the existing 16-screen structure by making the publication screen conditional. A separate permanent screen is not required if it makes the dock and map harder to understand.

Required visible changes:

- Free plan card says `Первая генерация и приватное превью` and explicitly says `Без публичной публикации`.
- Pro and Business continue to show publication, with the hosting distinction explained before the final release action.
- Free preview's primary completion action leads to the locked publication screen.
- The locked screen keeps the generated result visible and explains exactly what payment unlocks.
- Paid activation reached from publication uses `Вернуться к публикации`.
- The final publication action and summary match the selected hosting mode.
- Subscription management explains the different outcome after cancellation for Omnia hosting and own VPS.

## 8. Error Handling and Recovery

- Free direct publish action: remain on the locked publication state and show the upgrade action.
- Pending payment: publication remains locked.
- Failed/cancelled/expired payment: preserve the project and offer a retry.
- Paid plan changed back to Free before publication: clear paid publication access but keep the project.
- Hosting mode changed: invalidate only the mode-specific confirmation, not the project or paid status.
- VPS verification failure: keep VPS inputs in the local demonstration and allow retry or switch to Omnia hosting.
- Omnia-hosted plan expiry scenario: show paused public status and a restore-plan action.
- Existing VPS installation scenario: keep the application marked online while managed update actions are locked.

## 9. Verification

### 9.1 Structural and behavior checks

- Free activation does not set paid or succeeded state.
- Free can complete generation and preview.
- Free cannot publish through the button, dock, map, keyboard activation, or direct action dispatch.
- Upgrade preserves all project and publication fields.
- Pending payment cannot unlock publication.
- Confirmed Pro and Business return to the ready publication screen.
- Omnia hosting publishes only after its consequence confirmation.
- VPS installation publishes only after VPS verification and responsibility confirmation.
- Omnia expiry pauses the hosted app without deleting project state.
- VPS installation remains online after subscription expiry; managed update controls become locked.
- Reset restores the new defaults.
- The artifact performs zero external requests.

### 9.2 Browser checks

Verify both hosting branches and the Free paywall at:

- 1440 × 900;
- 1024 × 768;
- 390 × 844;
- keyboard-only navigation;
- 200% text zoom;
- reduced motion.

There must be no horizontal page overflow, clipped primary actions, browser exceptions, or console errors. The existing visual language and navigation must remain recognizable.

## 10. Out of Scope

- Production API, database, billing, deployment, or MAX Partner changes.
- Real payment or real VPS credentials.
- Final legal terms or an intellectual-property opinion.
- Counting production publication slots.
- Export/download implementation.
- A promise of indefinite free Omnia hosting.

