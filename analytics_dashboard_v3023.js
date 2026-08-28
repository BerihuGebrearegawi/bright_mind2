/**
 * BMT V30.23 - Analytics dashboard helpers.
 * Authorization is enforced by the backend.
 */

export async function getTeacherOverview(signal) {
  const r = await fetch("/api/teacher/analytics/overview", {
    credentials: "include", signal
  });
  if (!r.ok) throw new Error(`Teacher analytics failed (${r.status})`);
  return r.json();
}

export async function getAdminOverview(signal) {
  const r = await fetch("/api/admin/analytics/overview", {
    credentials: "include", signal
  });
  if (!r.ok) throw new Error(`Admin analytics failed (${r.status})`);
  return r.json();
}

export async function getAdminRevenue({from, to, signal} = {}) {
  const params = new URLSearchParams();
  if (from) params.set("from", from);
  if (to) params.set("to", to);

  const r = await fetch(`/api/admin/analytics/revenue?${params}`, {
    credentials: "include", signal
  });
  if (!r.ok) throw new Error(`Revenue analytics failed (${r.status})`);
  return r.json();
}
