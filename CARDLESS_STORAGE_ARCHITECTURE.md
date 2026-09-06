# BMT Cardless Storage Architecture

## Production architecture
- Render: Flask application server and protected API endpoints.
- Firestore: users, roles, progress, chat, metadata, URLs and indexes.
- Cloudinary: primary binary storage for images, videos, audio and PDFs/documents.
- Google Drive / ImgBB: legacy compatibility only for existing content.

## Why Google Cloud Storage is not the primary provider yet
A new Google Cloud Storage setup normally requires a valid Google Cloud billing/payment method. Because the BMT owner does not have a credit card, the production architecture uses Cloudinary's no-card Free plan instead. Cloudinary's current documentation states that the Free plan does not require a credit card.

## Cloudinary capability
Cloudinary supports image, video and PDF uploads and can use `auto` resource type detection.

## Required Cloudinary setting
The existing unsigned upload preset `bright-mind-tutor` must allow the resource types/formats BMT uploads: images, video/audio and PDF/documents. If it is restricted to video only, PDF/image uploads will fail.

## Migration rule
Existing Firestore records are not rewritten. Existing Google Drive/ImgBB URLs continue to work. New uploads go to Cloudinary. This avoids a risky bulk migration and removes the dependency on Google Drive/ImgBB for future growth.

## Future upgrade
When a valid Google Cloud billing method becomes available, a Cloud Storage adapter can be added behind the same storage router without changing the Firestore data model or user-facing upload screens.
