# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Readers following one shared courier's diary over time. The first local version runs on the author's MacBook; the original project also targets challenge judges and eventual Raspberry Pi operation.

## Product Purpose

A local model writes a small life as it happens. Readers observe; they cannot request entries, fast-forward, or change the courier's world.

## Operating Context

World time follows real UTC. A worker completes one tick, then rests one real hour. Entries are occasional, immediately published, and immutable. Blank periods are part of the live chronicle, not missing content to fill. The user later authorized explicitly backdated history starting October 23 UTC: it must be labelled as generated later and must not rewind or replace the live world.

## Capabilities and Constraints

The simulation owns facts and legal choices; Gemma selects actions and writes prose. SQLite persists the world, attempts, events, and published entries. The web process reads published entries without calling the model or advancing the world. All visible times must be explicitly UTC. Downtime is sleep during 22:00–06:00 UTC and mundane otherwise; live events can happen at night.

The local MVP uses Python, uv, FastAPI, synchronous SQLAlchemy, Docker Compose, and bind-mounted SQLite. Native llama.cpp supplies Gemma inference. No auth, user input, per-user lives, public deployment, doodles, replay, or markdown export in this goal.

## Brand Commitments

Name: Still. The diary voice is plain, first-person past tense, concrete and terse, without reader address, stated emotions, figurative language, or explanations of the metaphor. Do not turn the reader into the diarist's addressee.

## Evidence on Hand

`README.md`, the confirmed project goal, and the original Still project note establish the product. The local database contains real model-written entries; viewers must not substitute sample prose or fabricated activity.

## Product Principles

- Keep facts in code and expression in the model.
- Silence is legitimate; never generate to populate a screen.
- Reader requests have no effect on the life.
- Preserve history and make UTC unambiguous.
