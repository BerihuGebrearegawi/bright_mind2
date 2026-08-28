// static/storage-service.js
// ==========================================================
// 🗄️ STORAGE SERVICE — Unified Upload API
// ==========================================================

import { StorageRouter, GOOGLE_DRIVE_CONFIG, CLOUDINARY_CONFIG } from './storage-config.js';
import { getAuth } from 'https://www.gstatic.com/firebasejs/10.7.1/firebase-auth.js';

// ==========================================================
// PRIMARY CLOUDINARY UPLOAD
// ==========================================================
async function uploadToCloudinary(file, resourceType = 'auto') {
    try {
        const auth = getAuth();
        const user = auth.currentUser;
        if (!user) throw new Error('Please sign in before uploading media.');

        const cloudName = CLOUDINARY_CONFIG.cloudName;
        const uploadPreset = CLOUDINARY_CONFIG.uploadPreset;
        if (!cloudName || !uploadPreset) throw new Error('Cloudinary upload configuration is missing.');

        const formData = new FormData();
        formData.append('file', file);
        formData.append('upload_preset', uploadPreset);

        const response = await fetch(`https://api.cloudinary.com/v1_1/${encodeURIComponent(cloudName)}/${resourceType}/upload`, {
            method: 'POST',
            body: formData
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.secure_url) {
            throw new Error(data.error?.message || `Cloudinary upload failed (${response.status})`);
        }
        return {
            success: true,
            url: data.secure_url,
            provider: 'cloudinary',
            fileId: data.public_id,
            resourceType: data.resource_type || resourceType,
            bytes: data.bytes || file.size,
            format: data.format || ''
        };
    } catch (error) {
        console.error('Cloudinary upload error:', error);
        return { success: false, error: error.message };
    }
}

// ==========================================================
// GCS DOCUMENT UPLOAD
// ==========================================================
async function uploadToGCS(file) {
    try {
        const auth = getAuth();
        const user = auth.currentUser;
        if (!user) throw new Error('Please sign in before uploading a document.');
        const token = await user.getIdToken(true);
        const formData = new FormData();
        formData.append('file', file);
        const response = await fetch('/api/storage/document', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${token}` },
            body: formData
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.success) throw new Error(data.error || `GCS upload failed (${response.status})`);
        return data;
    } catch (error) {
        console.error('GCS document upload error:', error);
        return { success: false, error: error.message };
    }
}

// ==========================================================
// 4. MAIN UPLOAD FUNCTION
// ==========================================================
export async function uploadFile(file, fileType = 'auto') {
    let type = fileType;
    if (type === 'auto') {
        if (file.type.startsWith('image/')) type = 'images';
        else if (file.type === 'application/pdf' || file.type.includes('word')) type = 'documents';
        else if (file.type.startsWith('video/')) type = 'videos';
        else if (file.type.startsWith('audio/')) type = 'audio';
        else type = 'other';
    }
    
    const config = StorageRouter[type];
    if (!config) {
        return { success: false, error: 'Unknown file type' };
    }
    
    if (file.size > config.maxSize) {
        return { 
            success: false, 
            error: `File too large. Max ${config.maxSize / (1024 * 1024)}MB` 
        };
    }
    
    if (config.allowedTypes[0] !== '*' && !config.allowedTypes.includes(file.type)) {
        return { 
            success: false, 
            error: `File type ${file.type} not allowed for ${type}` 
        };
    }
    
    let result;
    // Architecture: images/videos/audio -> Cloudinary; books/PDF/DOC/DOCX -> GCS.
    if (type === 'documents') {
        result = await uploadToGCS(file);
    } else {
        const resourceType = config.resourceType || 'auto';
        result = await uploadToCloudinary(file, resourceType);
    }

    if (result.success) {
        console.log(`✅ Uploaded to ${result.provider}: ${result.url}`);
    } else {
        console.error(`❌ Upload failed: ${result.error}`);
    }
    
    return result;
}


// ==========================================================
// 🔐 ADMIN MEDIA UPLOAD
// Server-authoritative Cloudinary upload. Unlike uploadFile(), this endpoint
// requires an admin Firebase ID token, so the public Cloudinary upload preset
// cannot be abused to upload arbitrary media.
// ==========================================================
export async function uploadAdminMedia(file, resourceType = 'auto') {
    try {
        const auth = getAuth();
        const user = auth.currentUser;
        if (!user) throw new Error('Admin session expired. Please sign in again.');
        const token = await user.getIdToken(true);
        const formData = new FormData();
        formData.append('file', file);
        formData.append('resourceType', resourceType);
        const response = await fetch('/api/admin/storage/cloudinary', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${token}` },
            body: formData
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.success) {
            throw new Error(data.error || `Admin media upload failed (${response.status})`);
        }
        return data;
    } catch (error) {
        console.error('Admin media upload error:', error);
        return { success: false, error: error.message };
    }
}

// ==========================================================
// 7. INITIALIZE STORAGE
// ==========================================================
export function initStorage() {
    console.log('🗄️ Storage Service initialized');
    console.log('📸 Images → Cloudinary');
    console.log('📚 Books/PDF/DOC/DOCX → Google Cloud Storage');
    console.log('🎥 Videos → Cloudinary (100MB)');
    console.log('🎤 Audio → Cloudinary (10MB)');
}