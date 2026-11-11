# DESIGN.md · Kolins Design System Tokens & Guidelines

## Surface Mode: Operate
This surface is an operations console for enterprise sysadmins and helpdesk engineers. The UI must disappear into the workflow.

## Color Tokens
```css
:root {
  /* Surfaces */
  --bg-canvas: #090d16;
  --bg-surface: #111827;
  --bg-surface-elevated: #1f2937;
  --bg-surface-hover: #283548;
  --bg-subtle: #1e293b;
  
  /* Borders */
  --border-subtle: rgba(255, 255, 255, 0.08);
  --border-muted: rgba(255, 255, 255, 0.14);
  --border-focus: #3b82f6;

  /* Text & Contrast (WCAG AA Compliant) */
  --text-primary: #f8fafc;        /* 15.8:1 on #090d16 */
  --text-secondary: #cbd5e1;      /* 10.2:1 on #090d16 */
  --text-muted: #94a3b8;          /* 5.6:1 on #090d16 - fully AA compliant */
  --text-placeholder: #94a3b8;

  /* Primary Brand (Enterprise Cobalt) */
  --primary: #2563eb;
  --primary-hover: #1d4ed8;
  --primary-subtle: rgba(37, 99, 235, 0.15);
  --primary-border: rgba(37, 99, 235, 0.35);

  /* Semantic */
  --emerald: #10b981;
  --emerald-bg: rgba(16, 185, 129, 0.12);
  --emerald-border: rgba(16, 185, 129, 0.3);

  --rose: #f43f5e;
  --rose-bg: rgba(244, 63, 94, 0.12);
  --rose-border: rgba(244, 63, 94, 0.3);

  --amber: #f59e0b;
  --amber-bg: rgba(245, 158, 11, 0.12);
  --amber-border: rgba(245, 158, 11, 0.3);
}
```

## Typography Scale
- Heading 1 (App title): 18px / 600 / tracking -0.015em
- Heading 2 (Section title / Modal title): 16px / 600
- Body / Names: 15px / 600
- Labels & Primary UI: 13px / 500
- Secondary Meta & Tags: 12px / 500 (Strict floor: no text below 12px)

## Elevation & Shadows
- Cards: `box-shadow: 0 4px 12px rgba(0, 0, 0, 0.35);`
- Modals: `box-shadow: 0 16px 36px rgba(0, 0, 0, 0.6);`
- No zero-offset glows or colored halos.
