// static/storage-config.js
// ==========================================================
// 🗄️ STORAGE CONFIGURATION — Cardless Multi-Provider System
// ==========================================================

// Legacy/manual provider is supported only by the archive migration path.
const GOOGLE_DRIVE_CONFIG = {
    clientId: '782512714975-avqgntjttthfn1v297sjrer66ugigga990.apps.googleusercontent.com',
    folderId: '12Ah47bfdOnhE-HutNuS-IyghpATUA6MR'
};


// Primary binary storage provider. Public configuration only.
const CLOUDINARY_CONFIG = {
    cloudName: 'li81co9v',
    uploadPreset: 'bright-mind-tutor'
};

// New uploads: images/videos/audio -> Cloudinary; books/documents -> Google Cloud Storage.
export const StorageRouter = {
    images: { provider: 'cloudinary', resourceType: 'image', maxSize: 15 * 1024 * 1024, allowedTypes: ['image/jpeg','image/png','image/gif','image/webp'] },
    documents: { provider: 'gcs', resourceType: 'raw', maxSize: 100 * 1024 * 1024, allowedTypes: ['application/pdf','application/msword','application/vnd.openxmlformats-officedocument.wordprocessingml.document'] },
    videos: { provider: 'cloudinary', resourceType: 'video', maxSize: 100 * 1024 * 1024, allowedTypes: ['video/mp4','video/webm','video/ogg'] },
    audio: { provider: 'cloudinary', resourceType: 'auto', maxSize: 10 * 1024 * 1024, allowedTypes: ['audio/webm','audio/mpeg','audio/mp3','audio/ogg','audio/wav'] },
    other: { provider: 'cloudinary', resourceType: 'auto', maxSize: 100 * 1024 * 1024, allowedTypes: ['*'] }
};

export { GOOGLE_DRIVE_CONFIG, CLOUDINARY_CONFIG };
