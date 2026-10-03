# Word Chef — Release Summary

## VERSION
`v1.2.7` · 2026-10-03 · visual refresh + release hardening

## BASELINE
Previous public release: `v1.2.6`.

## HIGHLIGHTS
* Complete kitchen-themed visual refresh across home, gameplay, Grand Tour,
  multiplayer lobby/game/results and victory states.
* Responsive/mobile polish, safe-area handling, stronger focus-visible states
  and reduced-motion support.
* Gameplay rules, scoring and protocol behavior remain unchanged by the visual
  refresh.
* Frontend CI now verifies the production export, TypeScript, bundle size,
  viewport and self-contained asset constraints.
* Backend CI now builds the shipped frontend, runs the complete pytest battery
  and runs Prolepsis production acceptance.

## RELEASE VALIDATION
* Frontend CI: **PASS**.
* Backend CI: **PASS**.
* Backend gate includes the complete pytest battery and Prolepsis acceptance.
* Main commit prepared for release: current `main` after this release batch.

## KNOWN LIMITATIONS
1. Multiplayer remains single-process with SQLite; multi-host deployment needs
   the documented shared-store architecture.
2. Browser E2E uses build/API/WebSocket smoke coverage rather than a real
   headless-browser tap suite.
3. Docker registry publishing remains a deployment concern outside the
   repository build verification gate.

## DEPLOYMENT
`docker compose up --build` builds the static frontend and backend image.
Persistent state lives in the configured data volume.

## RELEASE STATUS
**RELEASE READY**

Automated frontend and backend gates are green. The repository is ready for
the `v1.2.7` tag/public release publication.
