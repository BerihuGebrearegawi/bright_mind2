# Bright Mind Tutor V22 — Payment & Entitlement Engine 2.0

V22 keeps the existing Flask + Firestore architecture and adds a server-authoritative payment verification flow.

## Payment lifecycle

1. Student submits provider + transaction ID through `/api/payments/submit`.
2. Flask creates a deterministic payment ID from `provider + transactionId`.
3. Telebirr/CBE gateway sends a signed webhook to `/api/payments/webhook/<provider>` after the gateway itself verifies the payment.
4. Flask verifies the HMAC signature, matches the existing payment, validates the amount, and atomically:
   - marks the payment `Verified`
   - creates/extends the student's `entitlements/{uid}` record
   - sets `users/{uid}.isPaid = true`
   - updates subscription expiry
   - records verification source
5. A notification is created and the student's existing Firestore listener refreshes premium content immediately.

## Important security rule

A transaction ID by itself is **not** proof of payment. V22 does not invent or fake Telebirr/CBE verification. The real gateway integration must provide a signed callback/API verification mechanism.

Set these Render environment variables when the corresponding gateway webhook is available:

- `TELEBIRR_WEBHOOK_SECRET`
- `CBE_WEBHOOK_SECRET`

The webhook signature expected by the generic adapter is HMAC-SHA256 in `X-BMT-Signature` (hex, with optional `sha256=` prefix).

If a gateway uses a different signature format, only the adapter/signature verification function needs to be changed. The entitlement transaction remains unchanged.

## Idempotency

The payment document ID is a SHA-256-derived stable ID. Repeated webhook delivery for the same provider transaction cannot create a second entitlement. Active subscriptions are extended from their current expiry rather than accidentally shortened.

## Existing storage strategy preserved

- Google Drive: PDFs/documents
- Cloudinary: videos
- ImgBB: images
- Firestore: metadata, users, progress, payments and entitlements

## Firestore rules

V22 also fixes the live-class rule placement so every `match` is inside the Firestore `documents` scope. Browser writes to payments and entitlements remain disabled.
