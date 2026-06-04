# Project Settings Tabs and Role Editor Redesign

**Date:** 2026-06-04
**Status:** Approved design

## Summary

Refine the project settings navigation and custom-role editor without changing
their behavior. The tabs will use text-only labels in a more useful order, and
the create/edit role form will become a guided, clearly sectioned editor.

## Goals

- Remove numeric badges from settings tabs so labels determine tab width.
- Rename the `Members` tab to `Users`.
- Place `User Roles` immediately after `Users`.
- Make the custom-role editor easier to scan and understand.
- Preserve the existing form fields, APIs, JavaScript behavior, and permission
  model.
- Keep the layout responsive and consistent with the current Fluxito settings
  visual language.

## Non-goals

- No RBAC model, permission, API, or validation changes.
- No changes to role assignment behavior.
- No redesign of the built-in role cards, role capability matrix, or custom
  roles list.
- No new multi-step wizard or modal.

## Navigation Design

The settings tabs will appear in this order:

1. `Users`
2. `User Roles` for owners and admins
3. `Connections`
4. `Notifications` for owners and admins
5. `General`

All numeric tab badges will be removed. Existing `data-tab`, `onclick`, and
panel identifiers remain unchanged so tab switching behavior is unaffected.

References within the User Roles panel that currently say `Members tab` or
`Manage members` will become `Users tab` and `Manage users`.

## Role Editor Design

Use the approved **Guided sections** layout within one inline card.

### Header

The editor header contains:

- The existing dynamic title, `Create role` or `Edit role`.
- A short sentence explaining that the editor controls tools and data sources.
- A subtle state label that distinguishes a new role from an edited role.

### Sections

The form is divided into four visually distinct sections:

1. **Role details**  
   Name and description fields, with brief guidance about choosing a clear
   access-profile name.

2. **Tool permissions**  
   The existing read/write matrix, including select-all controls. The matrix
   remains horizontally scrollable where needed.

3. **Provider access**  
   The existing searchable provider multi-select and chips, with guidance that
   selected providers define which connected data sources the role can use.

4. **Advanced access**  
   Existing scripting and generic-tool checkboxes in a visually distinct,
   caution-oriented area because these grants have broader capabilities.

Each section uses a numbered marker, a title, and a short explanation to create
a clear reading order. Sections remain visible together; there is no wizard or
accordion state.

### Action Footer

The existing Create/Save, Cancel, and status-message elements remain in a
contained footer at the bottom of the editor. Existing IDs and JavaScript
behavior remain unchanged.

## Responsive Behavior

- On wide screens, role name and description remain side by side.
- On narrow screens, fields stack and section padding reduces.
- The tool-permission table remains horizontally scrollable.
- Footer actions remain accessible and may wrap or stack when space is limited.

## Implementation Scope

The change is contained in:

- `app/templates/projects/settings.html`

The template markup and its colocated CSS will be updated. Existing element IDs,
form names, `data-*` attributes, and event hooks required by the JavaScript will
be preserved.

## Verification

- Confirm tab labels, order, visibility rules, and switching behavior for an
  owner/admin.
- Confirm a regular member sees the expected subset of tabs with `Users` first.
- Confirm create-role submission still sends the same payload.
- Confirm edit-role loading, save, cancel, select-all controls, provider search,
  and advanced toggles still work.
- Visually inspect desktop and mobile layouts.

