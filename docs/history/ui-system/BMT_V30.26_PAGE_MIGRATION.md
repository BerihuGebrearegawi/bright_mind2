# Bright Mind Tutor V30.26 — Page Migration Plan

## Purpose
Migrate existing pages toward the V30.25 shared UI system without rewriting
working business logic.

## Non-destructive rule
Existing JavaScript business logic, API calls, authentication and database
logic must remain unchanged unless a separate bug/security fix is required.

## Migration markers

Pages can opt into the shared system with these attributes:

- `data-bmt-page`
- `data-bmt-page-header`
- `data-bmt-card`
- `data-bmt-table`
- `data-bmt-primary`
- `data-bmt-secondary`
- `data-bmt-accent`
- `data-bmt-status`
- `data-bmt-focus`

The helper `bmt-v30.26-migration.js` converts these markers into the V30.25
design-system classes.

## Migration order

1. Global page shell
2. Dashboard pages
3. Forms
4. Tables/lists
5. Status badges
6. Action buttons
7. Empty/loading/error states
8. Responsive QA

## QA gate

For every migrated page verify:

- Page loads
- Authentication state is preserved
- Existing API calls still work
- Existing event handlers still work
- Forms still submit
- Tables still render
- No console-breaking JavaScript errors are introduced
- Desktop layout works
- Tablet layout works
- Mobile layout works
- Keyboard focus is visible

## Important

Do not claim a page is fully migrated merely because the CSS class was added.
The page is complete only after visual and functional QA.
