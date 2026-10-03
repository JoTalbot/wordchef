"""Frontend smoke tests — the shipped bundle is the real Next.js export."""
from __future__ import annotations

from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
OUT = FRONTEND / "out"


class TestFrontendBuild:
    def test_export_exists(self):
        assert (OUT / "index.html").is_file(), "run `npm run build` in frontend/"

    def test_index_contains_game_shell(self):
        html = (OUT / "index.html").read_text(encoding="utf-8")
        assert "Word Chef" in html
        assert "_next" in html

    def test_js_bundle_contains_client_protocol(self):
        chunks = list((OUT / "_next" / "static").rglob("*.js"))
        assert chunks, "no JS chunks exported"
        blob = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in chunks)
        # the client must speak the server protocol. The markers below are the
        # intents and endpoints that actually cross the wire — engine.apply_intent
        # accepts SUBMIT_DISH / PREP / GOLDEN / RING_BELL / SKIP, spice is a flag
        # on SUBMIT_DISH (`use_spice`). Do not assert on UI labels here: they are
        # localised, the protocol is not.
        for marker in ("/api/matches", "/api/players", "SUBMIT_DISH", "use_spice",
                       "PREP", "GOLDEN", "RING_BELL", "SKIP"):
            assert marker in blob, f"missing protocol marker {marker}"

    def test_bundle_stays_small(self):
        total = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
        assert total < 4 * 1024 * 1024, "mobile-first: keep the bundle lean"

    def test_no_external_cdn_references(self):
        """The preview sandbox has no network — assets must be self-contained."""
        html = (OUT / "index.html").read_text(encoding="utf-8")
        for marker in ("cdn.", "unpkg.com", "fonts.googleapis"):
            assert marker not in html

    def test_mobile_viewport(self):
        html = (OUT / "index.html").read_text(encoding="utf-8")
        assert "width=device-width" in html
