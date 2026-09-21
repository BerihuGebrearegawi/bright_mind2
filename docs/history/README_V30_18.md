# Bright Mind Tutor — V30.18

## Live Class Scheduler Upgrade

- Grade 3–12 scheduling remains supported.
- Added optional `classGroupId` / section support (for example `8A`).
- Added `timeZone` metadata, defaulting to `Africa/Addis_Ababa`.
- Teacher overlap detection is enforced server-side.
- Class-group overlap detection prevents two simultaneous classes for the same section.
- Update/PATCH operations re-check scheduling conflicts.
- Student enrollment validates grade and, when available, class group/section.
- Teacher UI now shows the Addis Ababa/EAT timezone and optional class group field.

All scheduling timestamps are stored as UTC instants, with `timeZone` retained for display/business context.
