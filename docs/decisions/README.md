# Architecture decision records

One file per decision: `NNN-short-title.md`. Use this layout:

```
# NNN: Title
Status: proposed | accepted | superseded by NNN
Date: YYYY-MM-DD

## Context
## Options considered
## Decision
## Consequences
## Evidence
(links to spike notes, measurements, commits)
```

Product-level decisions (scope, audience) live in the decisions log in `PLAN.md` §1. ADRs cover technical choices.

| # | Title | Status |
|---|---|---|
| [001](001-base-translation-kit.md) | Base translation kit: fork recomp-kit (pinned `eb32cc4`) | accepted 2026-10-14 |
| [002](002-exact-x87.md) | Exact x87: hybrid double fast path + SoftFloat-80 slow path | proposed: accept after legion vectors pass |
