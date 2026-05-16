# Trust Analytics Portal Design System

This file captures the ASK workspace as the source of truth for the rest of the portal. New pages should reuse these rules before adding new visual patterns.

## Product Feel

- Enterprise AI copilot, not a marketing page or BI dashboard.
- The interface should feel governed, traceable, and action-oriented.
- The first visual priority is the user's task: shape a business question, validate it, then package outputs.
- Use progressive disclosure for evidence and secondary controls.

## Typography

- UI font: `Geist`.
- Question-entry font: `Inter`, used only for the large natural-language input so it reads as typed product text rather than a page heading.
- Numeric / technical font: `Geist Mono`.
- Body copy: 14px, regular or medium.
- Primary workspace headings: 28px, 650 weight, tight but readable.
- Question input: 19px, 400 weight, 1.55 line-height, `Inter`, normal letter spacing.
- Field labels: 14-15px, medium, no all-caps.
- Section labels / metadata: 10-11px, `Geist Mono`, uppercase, 0.12em letter spacing.

## Layout

- Desktop-first minimum width: 1180px.
- Main ASK grid: left flow rail, central workspace, right inspector panel.
- Flow rail width: about 180px.
- Right inspector width: about 386px.
- Central workspace owns the primary attention; right panel supports status and packaging.
- Use 24-32px page padding and 20-28px gaps between major columns.

## ASK Controls

- The question input is the hero surface. It uses a blue focused border, subtle blue glow, and a footer with copilot state and send action.
- AI suggestions appear as a single-row suggestion strip with a small spark icon and compact outline buttons.
- Business objective is a select-style control with target icon, selected text, clear action, and chevron.
- Time period is a segmented control with four segments plus a calendar button.
- Object / segment is a token selector with removable tokens.
- Additional context is an optional drawer with empty-state select controls.
- Recommended path is a blue-tinted evidence rail with database icon and source lineage.

## Icons

- Use inline stroke SVG icons from `Icon()` in `web/assets/app.js`.
- Icons are functional, not decorative. Prefer target, calendar, users, table, database, shield, mail, download, presentation, warning, check, search, chevron.
- Avoid emoji and large decorative icon circles.
- Artifact icons may use solid file-like tiles with muted product colors.

## Color

- Background: off-white / pale grey `#f7f8fa`.
- Panels: white.
- Text: deep navy `#111a2c`.
- Primary blue: `#0d63ce`.
- Trust green/teal: `#178468`.
- Warning amber: `#b27312`.
- Borders: cool grey `#dce3eb`.
- Use color for state, evidence, and action hierarchy. Do not turn the page into a single blue theme.

## Page Pattern

- `/` establishes the design language and should remain the canonical reference.
- `/analysis/:id` should reuse the same header, typography, action buttons, artifact treatment, and evidence rail tone.
- `/review/:id` should feel like a denser evidence room, but still use the same typography, tabs, cards, and icon system.
- `/handoff/:id` should keep the same system while emphasizing blocked decisions with amber/red state treatment.
