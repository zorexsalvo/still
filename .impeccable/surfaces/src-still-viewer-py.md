---
version: 1
slug: "src-still-viewer-py"
primary_target: "src/still/viewer.py"
related_targets: ["src/still/static/diary.css"]
---

# Diary viewer

Mode: Read. Scope: `/`, published entries plus read-only recorded world status. Purpose and constraints come from the confirmed MVP goal; visual details are a minimal implementation baseline, not a new user-approved brand direction.

## Direction contract

THESIS: A dated chronicle, not a simulation dashboard or a terminal costume. Actual diary text leads; blanks remain blanks.

OWN-WORLD: Cool white ground, dark blue-gray ink, readable Georgia prose, system sans date apparatus, blue underlined links. No cards, external imagery, pseudo-paper, or decorative motion. The user later approved a minimal static line-art scene in the background margin: Still arriving, reading, waiting or with a recorded interaction partner. Operational status belongs quietly in the footer.

STORY: The reader sees the latest published words, knows precisely which UTC moment they belong to, and can follow earlier entries without influencing the life. Only complete blank UTC dates between entries are counted as gaps.

FIRST VIEWPORT: A compact Still masthead and UTC as-of time above one centered reading column. A static scene reflects the latest recorded moment. On desktop it stays outside the column; on small screens a compact horizontal vignette precedes the entries, never behind prose. The reader can inspect worker evidence in the footer without mistaking an illustration for a heartbeat. Refresh is a normal link; earlier entries paginate below the text. Error and empty views retain the same frame.

FORM: Dated chronicle, candidate 7; seed:b310af4a7baddb8f. Other considered source families: reading logs, postal records, field notes, published letters, plain-text files, manuscript pages. Challenger decoration is excluded where it obstructs the confirmed quiet reading task. Signature interaction is permalinked entries and conventional earlier/latest navigation, with native anchor scrolling and no animated content.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
