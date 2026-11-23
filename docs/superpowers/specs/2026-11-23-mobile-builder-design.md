# Mobile Yleum builder

The owner builds and manages an app from a phone and can keep Yleum on the home screen. The user requested a native app feel, generous controls, short labels, and a real mini app preview without a device mockup.

Use a shared three-item bottom navigation: Chat, Preview, Manage. Keep desktop navigation intact. On phones use one compact header, large grouped menu rows, 48px controls, 16px inputs, safe areas and a composer above the bottom navigation. The preview occupies the screen below a compact close header. Management uses a compact section switcher and cards with consistent spacing.

Approaches: enlarging the existing desktop controls was insufficient; a separate native app would duplicate the product. Extend the existing responsive React app and add a standalone web manifest. This is the implementation choice following the user's instruction to rework the design autonomously.

Install on Android through the browser install event when available, otherwise show platform instructions. iPhone uses Share / Add to Home Screen. Reuse the brand icon. Installation is optional. No service worker caching of authenticated responses, no offline or native-store claims. Preserve authentication, drafts, ownership, generation and billing behavior.

## Mobile review of the project list and management

Shopify is a close reference for a product owner who edits a storefront, previews it and processes orders on the same phone. Its mobile editor puts store configuration in the store menu. Opening an order takes the merchant to that order's details. [Shopify mobile editor](https://help.shopify.com/en/manual/shopify-admin/shopify-app/mobile-online-store) · [Shopify orders](https://help.shopify.com/en/manual/fulfillment/managing-orders/managing-order-details)

In Yleum, Chat, Preview and Manage stay within thumb reach. The project header opens all six project sections. Manage shows its enabled queues and settings in one visible row. A selected record gets the available width and a return action that restores the filter and focus. The wide layout shows both panes. [Android adaptive list and detail layout](https://developer.android.com/develop/ui/views/layout/canonical-layouts)

On the project list, Create sits next to the title and each starter idea takes one row. The creation form fills the viewport and exposes its next action at the bottom. Cancelling an empty form returns the owner to the project list on the next visit. Filled drafts keep their existing resume behavior.

Local acceptance covers 320 CSS pixels without horizontal page scrolling, 48-pixel primary controls, an editable field above the bottom navigation and a usable return path for every reviewed task. [W3C reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow) · [Apple buttons](https://developer.apple.com/design/human-interface-guidelines/buttons) · [Android touch targets](https://developer.android.com/design/ui/mobile/guides/foundations/accessibility)
