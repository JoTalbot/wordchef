# Word Chef — Release Summary

## VERSION
`v1.1.0` · 2026-10-03 · visual refresh + release hardening

## HIGHLIGHTS
* Complete kitchen-themed visual refresh across home, gameplay, Grand Tour,
  multiplayer lobby/game/results and victory states.
* Responsive/mobile polish, safe-area handling, stronger focus-visible states
  and reduced-motion support.
* Existing game rules, scoring and protocol remain unchanged by the visual work.
* CI now verifies the frontend production export and the backend/game release
  battery automatically.
* Full backend release gate: frontend export build + pytest suite + Prolepsis
  production acceptance.

## ARCHITECTURE
`Next.js 14 (TS, static export)` ⇄ `FastAPI + WebSocket + SQLite` ⇄
`Prolepsis v0.39.0`.
The game core remains deterministic and server-authoritative.

## RELEASE VALIDATION
* Frontend CI: production build, TypeScript check, exported bundle checks,
  mobile viewport and no-CDN validation.
* Backend CI: frontend export build, full pytest battery and Prolepsis
  acceptance.
* Latest main backend CI: **PASS**.
* Latest backend run executed the frontend build, full pytest suite and
  Prolepsis acceptance successfully.

## KNOWN LIMITATIONS
1. Multiplayer remains single-process with SQLite; multi-host deployment
   requires the documented shared-store architecture.
2. Browser E2E uses build/API/WebSocket smoke coverage rather than a real
   headless-browser tap suite.
3. Docker registry publishing remains a deployment concern outside the
   repository's build verification gate.

## DEPLOYMENT
`docker compose up --build` builds the static frontend and backend image.
Persistent state lives in the configured data volume.

## RELEASE STATUS
**RELEASE CANDIDATE READY**

All automated gates currently visible in GitHub for the release path are green.
The remaining publication step is the GitHub release/tag operation itself.
