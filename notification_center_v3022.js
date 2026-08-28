/**
 * BMT V30.22 - Notification Center helpers.
 * The client can read notifications and update read state only.
 */

export async function getNotifications({limit = 30, signal} = {}) {
  const params = new URLSearchParams({limit: String(limit)});
  const r = await fetch(`/api/notifications?${params}`, {
    credentials: "include",
    signal
  });
  if (!r.ok) throw new Error(`Unable to load notifications (${r.status})`);
  return r.json();
}

export async function markNotificationRead(notificationId, signal) {
  const id = encodeURIComponent(notificationId);
  const r = await fetch(`/api/notifications/${id}/read`, {
    method: "POST",
    credentials: "include",
    signal
  });
  if (!r.ok) throw new Error(`Unable to mark notification read (${r.status})`);
  return r.json();
}

export async function markAllNotificationsRead(signal) {
  const r = await fetch("/api/notifications/read-all", {
    method: "POST",
    credentials: "include",
    signal
  });
  if (!r.ok) throw new Error(`Unable to mark notifications read (${r.status})`);
  return r.json();
}

export async function getNotificationPreferences(signal) {
  const r = await fetch("/api/notifications/preferences", {
    credentials: "include",
    signal
  });
  if (!r.ok) throw new Error(`Unable to load notification preferences (${r.status})`);
  return r.json();
}
