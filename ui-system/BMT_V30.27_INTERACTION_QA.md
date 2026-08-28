# Bright Mind Tutor V30.27 — Interaction QA

## Focus
Navigation, forms, tables and interaction quality.

## Added
- Optional duplicate-submit protection for forms.
- Optional accessible required-field validation hints.
- Keyboard-accessible sortable-table headers.
- Current-route navigation highlighting.
- Consistent disabled and validation visual states.

## Opt-in attributes
- `data-bmt-protect-submit`
- `data-bmt-validate`
- `data-bmt-validation-message`
- `data-bmt-sortable`
- `data-bmt-sort-key`
- `data-bmt-nav-link`

## QA checklist

### Navigation
- Current route is highlighted.
- Links point to valid targets.
- Back/forward navigation remains functional.
- No navigation logic is replaced.

### Forms
- Required fields validate.
- Invalid fields receive focus.
- Duplicate submit is prevented where opted in.
- Existing submit handlers/API calls remain intact.

### Tables
- Horizontal overflow works on small screens.
- Sort controls are keyboard accessible where implemented.
- Empty/loading/error states remain understandable.

### Interaction
- Disabled controls are visibly disabled.
- Keyboard focus is visible.
- Errors are actionable.
- No raw exceptions are displayed to users.

## Completion rule
This release adds QA infrastructure. A page is only COMPLETE after real functional
testing confirms its existing workflow still works.
