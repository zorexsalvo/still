---
name: Still
description: The local MVP's text-first diary viewer
colors:
  ground: "#f8fafb"
  ink: "#23323d"
  muted: "#52626d"
  link: "#1c536e"
  rule: "#d8e1e5"
  selection: "#d5e9f2"
typography:
  display:
    fontFamily: 'Georgia, "Times New Roman", serif'
    fontSize: "3rem"
    fontWeight: 400
    lineHeight: 1.1
  body:
    fontFamily: 'Georgia, "Times New Roman", serif'
    fontSize: "1.25rem"
    lineHeight: 1.7
  label:
    fontFamily: "system-ui, sans-serif"
    fontSize: "0.875rem"
    lineHeight: 1.5
  data:
    fontFamily: "system-ui, sans-serif"
    fontSize: "1rem"
  section:
    fontFamily: "system-ui, sans-serif"
    fontSize: "1.125rem"
  pageHeading:
    fontFamily: "system-ui, sans-serif"
    fontSize: "1.5rem"
  mobileDisplay:
    fontFamily: 'Georgia, "Times New Roman", serif'
    fontSize: "2.5rem"
spacing:
  reading-width: "45rem"
  entry-separation: "3rem"
  desktop-gutter: "1.5rem"
  mobile-gutter: "1.25rem"
---

# Design System: Still

## Overview

This records the implemented local-MVP baseline, not a separately approved visual rebrand. Actual diary prose is the content; interface copy stays outside it. The surface is a readable document rather than a simulation dashboard.

## Colors

Cool light ground, dark ink, quieter date apparatus, and one link color. Selection has its own pale blue field. Text contrast against the ground is 12.58:1 for ink, 6.03:1 for secondary text, and 7.99:1 for links.

## Typography

Georgia carries prose and the masthead; system sans carries dates, navigation, errors, and empty states. Dates use tabular numerals. The masthead becomes 2.5rem on narrow screens; prose retains its reading size.

## Layout

One centered reading column with generous separation between entries. The header pairs the title and UTC as-of time on desktop and stacks them below 36rem. Earlier/latest navigation uses normal links. Long unbroken text wraps within the column.

## Elevation & Depth

Flat. Thin horizontal rules separate the header and footer; entries have neither cards nor shadows.

## Components

Entry dates are underlined permalink headings. Earlier-page and refresh links remain visibly interactive; hover increases underline thickness. Keyboard focus uses a 2px link-colored outline with 5px offset. A skip link appears on focus. There are no forms, icon controls, loading animations, or client-side scripts.

Explicitly backdated entries carry a secondary-text provenance line below their unchanged prose, identifying their actual generation time and stating that they were not recorded live.

Diary and World share a small underlined section navigation with an explicit active-page state. The World view inherits the same palette and column: definition lists for the courier and clock, a monospace block only for literal file contents, plain lists for people/activity, and a semantic delivery table in a keyboard-focusable horizontal scroll region with a visible sideways-scroll hint on narrow screens. It has no controls that advance the simulation. It distinguishes backdated events, occasional visitors, and stale worker check-ins. Borrowed notes and their UTC return time appear in the same courier definition list; unresolved private threads and lending history use the existing plain lists and secondary text. These are observed facts, never action controls.

The user-approved ambient scene is authored inline SVG: Still's cap, coat and messenger bag, an entryway or a person actually involved in the latest tick, and a shared ground line, all in the rule color. Both sleeves are closed outlines with cuffs and three-finger hands, the shoulders are a single line, and the other figures in frame get the same hands so nobody is drawn half-handed at a smaller scale. Static arrival, reading/restoration, waiting and interaction poses illustrate recorded actions, not continuous live activity. The SVG is decorative and pointer-inert; its accessible caption names the moment and place. It occupies the outer margin above 72rem and a small horizontal vignette above the diary below that width, never behind prose. There is no pulse, polling or animation. Operational status stays in the footer so it does not displace the story. It uses normal secondary-text contrast and separates recent check-ins, completed live ticks, next eligibility, and errors. A missing status does not masquerade as a healthy worker.

**The Solid-Over-Line Rule.** A shape that must read as being in front — the satchel, the page Still is reading — carries a `.scene-solid` fill of the ground color and is drawn after the strokes it covers. Overlapping open outlines braid into a scribble at this size; occlusion is the only thing that reads as depth in a one-weight line drawing.

The empty and recoverable error views retain the document frame. Blank-date counts describe complete UTC dates, not simulated events or inferred mood.

### Publication assets

The favicon reduces the courier's satchel to a light silhouette on an ink-colored rounded-square field, with a flap and clasp readable at small sizes. Its SVG source and 16/32/48px ICO fallback are local static assets, not a loading or activity indicator.

The 1200 × 630 blog cover inherits the cool ground, Georgia title and established courier drawing in stronger ink for thumbnail legibility. The title and factual Gemma description occupy the left; an illustrative courier raising a hand toward a repair-shop doorway occupies the right. The shoulders are one line and the raised arm springs from its end, so the coat's side never doubles as an arm; both sleeves are closed outlines with cuffs and three-finger hands, and the satchel sits on the side away from the raised arm. Editable SVG and a direct PNG export live under `assets/branding/`; embedded raster provenance identifies the vector source. No stock imagery, image model or bundled font files are used. This is a brand-asset extension, not a new page identity.

## Do's and Don'ts

- Do keep UTC visible in the frame and individual timestamps.
- Do render actual published text unchanged and escaped.
- Do keep readable measures, visible link affordances, and keyboard access.
- Don't use demonstration text to fill a quiet live diary.
- Don't introduce motion or reader controls that influence the life without a new product decision.
