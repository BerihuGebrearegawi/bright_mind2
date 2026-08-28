/* Firebase Cloud Messaging service worker. Replace the public config below only if your Firebase project changes. */
importScripts('https://www.gstatic.com/firebasejs/10.7.1/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/10.7.1/firebase-messaging-compat.js');

firebase.initializeApp({
  apiKey: "AIzaSyAyeZpwu9-FECjC5Qp-lI0OUAblKusxkeI",
  authDomain: "bright-mind-tutor-app.firebaseapp.com",
  projectId: "bright-mind-tutor-app",
  storageBucket: "bright-mind-tutor-app.firebasestorage.app",
  messagingSenderId: "782512714975",
  appId: "1:782512714975:web:719e3b7a09ac8c7f9d256a"
});

const messaging = firebase.messaging();
messaging.onBackgroundMessage(payload => {
  const title = payload.notification?.title || 'Bright Mind Tutor';
  const options = { body: payload.notification?.body || 'You have a new learning notification.', icon: '/static/android-chrome-192x192.png', badge: '/static/favicon-32x32.png' };
  self.registration.showNotification(title, options);
});
