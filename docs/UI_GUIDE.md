# Cartify UI guide

## Design

The storefront uses a modern dark palette, responsive spacing, stronger card hierarchy, focus indicators, and reduced-motion support. The base template retains Tailwind, HTMX, the existing cart badge include, payment-mode label, and Jinja blocks.

## Storefront

The catalog page clarifies search, category selection, sorting, price range filters, stock status, and add-to-cart actions. Empty search results now include a clear-filter action. Product and payment behavior remains owned by the existing backend.

## Existing interactions preserved

- Catalog query parameters: `q`, `category`, `sort`, `min_price`, and `max_price`.
- Product detail links continue to use `/products/{slug}`.
- Add-to-cart still posts to `/cart/add` and updates `#cart-badge` with HTMX.
- Cart, wishlist, orders, admin, authentication, and logout links remain in the shared header.
- The fake and Stripe test-mode labels remain based on `payments_mode`.

## Verification checklist

1. Follow the root README quickstart and run the existing tests.
2. Verify search, category filters, sort, price filters, reset, product detail, cart updates, wishlist, and orders.
3. Check fake-payment success/failure and Stripe webhook tests using test keys only.
4. Verify mobile navigation and keyboard focus.

The UI update has not been runtime- or browser-tested as part of this change. Use demo data and test payments only.
