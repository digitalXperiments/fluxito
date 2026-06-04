# User Roles Compact Workflow Redesign

**Date:** 2026-06-04
**Status:** Approved design

## Summary

Redesign the User Roles tab around the repeat tasks: viewing custom roles,
creating roles, and editing roles. The built-in role explanation becomes a
collapsed reference, and create/edit moves into a large right-side drawer so
users do not need to scroll through the full page to reach or operate the
editor.

## Goals

- Make custom-role management visible immediately after opening User Roles.
- Let users create or edit a role without losing their position on the page.
- Keep all existing role-editor fields and behavior.
- Keep save and cancel actions visible while scrolling within the editor.
- Reduce page height by collapsing built-in role reference content by default.
- Provide a usable mobile experience.

## Non-goals

- No changes to RBAC permissions, APIs, validation, or role assignment.
- No changes to Owner, Admin, or Member capabilities.
- No bulk role actions or role duplication.
- No separate role-management route.

## Page Structure

The User Roles tab begins with a compact management header:

- Label and title: `Custom roles`.
- Short description explaining custom access profiles.
- Primary action: `Create role`.
- Secondary action: `Manage users`.

The custom roles list appears directly below the header. Existing roles remain
compact rows with name, description, Edit, and Delete actions. The empty state
includes a clear Create role action.

Below the custom-role area, a collapsed `Built-in roles and permissions`
disclosure contains:

- The Owner, Admin, and Member reference cards.
- The built-in role capability matrix.

The disclosure is collapsed by default and can be expanded without navigation.

## Role Editor Drawer

Create and Edit open the existing guided role editor inside a large right-side
drawer.

### Drawer behavior

- `Create role` resets the form, sets the title/state to new role, and opens the
  drawer.
- `Edit` loads the selected role into the existing form and opens the drawer.
- Cancel, close button, and clicking the backdrop close the drawer.
- Escape closes the drawer.
- Successful create or edit refreshes the role list and closes the drawer.
- The page behind the drawer does not scroll while the drawer is open.

### Drawer layout

- Desktop width is large enough for the permission matrix while leaving context
  visible behind it.
- The drawer header stays visible.
- The form body scrolls independently.
- The action footer stays visible at the bottom.
- Existing guided sections remain: Role details, Tool permissions, Provider
  access, and Advanced access.

### Mobile behavior

- The drawer becomes a full-screen sheet.
- The permission table remains horizontally scrollable inside its section.
- Header and footer remain fixed within the sheet.

## Accessibility

- The drawer uses dialog semantics with an accessible title.
- Opening the drawer moves focus to the role name field.
- Closing restores focus to the control that opened it.
- Close button has an accessible label.
- Escape and backdrop close behavior are supported.
- The built-in role disclosure uses native `details`/`summary` behavior.

## Implementation Scope

Modify:

- `app/templates/projects/settings.html`
- `tests/test_project_settings_template.py`

Preserve all existing role form field names, IDs, permission collection logic,
API endpoints, and role CRUD behavior.

## Verification

- Confirm Custom roles appears before built-in roles.
- Confirm built-in reference is collapsed by default.
- Confirm Create opens a reset drawer.
- Confirm Edit loads the selected role and opens the drawer.
- Confirm Cancel, close, backdrop, and Escape close the drawer.
- Confirm create/edit success refreshes roles and closes the drawer.
- Confirm provider dropdown and tool permissions work inside the drawer.
- Confirm desktop and mobile drawer layouts.

