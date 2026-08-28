/**
 * BMT V30.19 - Student Live Class Experience helpers.
 * Server-authoritative: this module never decides authorization.
 */

export function formatLiveClassTime(isoString, timeZone = "Africa/Addis_Ababa") {
  const d = new Date(isoString);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("en-ET", {
    timeZone,
    dateStyle: "medium",
    timeStyle: "short"
  }).format(d);
}

export function getLiveClassState(item, now = new Date()) {
  const start = new Date(item.startTime);
  const end = new Date(item.endTime);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return "INVALID";
  if (now < start) return "UPCOMING";
  if (now >= start && now < end) return "LIVE";
  return "COMPLETED";
}

/**
 * Fetch classes from the server. Authorization and grade/section filtering
 * must happen on the server.
 */
export async function fetchStudentLiveClasses({
  date,
  status,
  signal
} = {}) {
  const params = new URLSearchParams();
  if (date) params.set("date", date);
  if (status) params.set("status", status);

  const response = await fetch(`/api/live-classes/student?${params}`, {
    credentials: "include",
    signal
  });

  if (!response.ok) {
    throw new Error(`Unable to load live classes (${response.status})`);
  }

  return response.json();
}
