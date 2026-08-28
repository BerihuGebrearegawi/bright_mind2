/**
 * BMT V30.21 - Parent Portal API helpers.
 * The backend remains authoritative for parent-child authorization.
 */

export async function getParentChildren(signal) {
  const r = await fetch("/api/parent/children", {
    credentials: "include",
    signal
  });
  if (!r.ok) throw new Error(`Unable to load children (${r.status})`);
  return r.json();
}

export async function getChildProgress(childId, signal) {
  const safeId = encodeURIComponent(childId);
  const r = await fetch(`/api/parent/children/${safeId}/progress`, {
    credentials: "include",
    signal
  });
  if (!r.ok) throw new Error(`Unable to load progress (${r.status})`);
  return r.json();
}

export async function getChildAnalytics(childId, signal) {
  const safeId = encodeURIComponent(childId);
  const r = await fetch(`/api/parent/children/${safeId}/analytics`, {
    credentials: "include",
    signal
  });
  if (!r.ok) throw new Error(`Unable to load analytics (${r.status})`);
  return r.json();
}
