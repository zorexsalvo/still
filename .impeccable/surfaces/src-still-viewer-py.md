---
version: 1
slug: "src-still-viewer-py"
primary_target: "src/still/viewer.py"
related_targets: ["src/still/static/diary.css"]
---

# Diary viewer

Mode: Read. Scope: `/`, published entries only. Purpose and constraints come from the confirmed MVP goal; visual details are a minimal implementation baseline, not a new user-approved brand direction.

## Direction contract

THESIS: A dated chronicle, not a simulation dashboard or a terminal costume. Actual diary text leads; blanks remain blanks.

OWN-WORLD: Cool white ground, dark blue-gray ink, readable Georgia prose, system sans date apparatus, blue underlined links. No cards, imagery, pseudo-paper, or decorative motion.

STORY: The reader sees the latest published words, knows precisely which UTC moment they belong to, and can follow earlier entries without influencing the life. Only complete blank UTC dates between entries are counted as gaps.

FIRST VIEWPORT: A compact Still masthead and UTC as-of time above one centered reading column. Latest entries begin immediately below. Refresh is a normal link; earlier entries paginate below the text. Error and empty views retain the same frame.

FORM: Dated chronicle, candidate 7; seed:b310af4a7baddb8f. Other considered source families: reading logs, postal records, field notes, published letters, plain-text files, manuscript pages. Challenger decoration is excluded where it obstructs the confirmed quiet reading task. Signature interaction is permalinked entries and conventional earlier/latest navigation, with native anchor scrolling and no animated content.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
