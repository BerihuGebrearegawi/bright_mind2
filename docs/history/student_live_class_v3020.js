/**
 * BMT V30.20 - Student live-class client helpers.
 * Authorization and attendance state remain server-authoritative.
 */

export async function joinLiveClass(classId, signal) {
  const r = await fetch(`/api/live-classes/${encodeURIComponent(classId)}/join`, {
    method: "POST",
    credentials: "include",
    signal
  });
  if (!r.ok) throw new Error(`Join failed (${r.status})`);
  return r.json();
}

export async function leaveLiveClass(classId, signal) {
  const r = await fetch(`/api/live-classes/${encodeURIComponent(classId)}/leave`, {
    method: "POST",
    credentials: "include",
    signal
  });
  if (!r.ok) throw new Error(`Leave failed (${r.status})`);
  return r.json();
}

export async function getLiveClassRecording(classId, signal) {
  const r = await fetch(`/api/live-classes/${encodeURIComponent(classId)}/recording`, {
    credentials: "include",
    signal
  });
  if (!r.ok) throw new Error(`Recording unavailable (${r.status})`);
  return r.json();
}

export async function getLiveClassSummary(classId, signal) {
  const r = await fetch(`/api/live-classes/${encodeURIComponent(classId)}/summary`, {
    credentials: "include",
    signal
  });
  if (!r.ok) throw new Error(`Summary unavailable (${r.status})`);
  return r.json();
}
