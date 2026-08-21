from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVENANCE = ROOT / "docs" / "UPSTREAM_REFERENCE_PROVENANCE.md"
SITE_DATA = (
    ROOT
    / "plugins"
    / "evidence-lane-plugin"
    / "remote_adapter"
    / "app"
    / "_data"
    / "upstream-references.ts"
)


REFERENCES = {
    "https://github.com/github/gh-aw": (
        "5107b7bd547ce124f69084dc623e45a539cd17f5",
        "1fa5cd684c6906a2f7bca8468097292fe4f6a40a",
        "MIT",
    ),
    "https://github.com/github/gh-aw-mcpg": (
        "0edcc73107c5a08b5bd8cfd489e68400b5ef95db",
        "58482ae9b60a14327f48f77e71ffd2d1930cf8ea",
        "MIT",
    ),
    "https://github.com/github/copilot-sdk": (
        "4e95deee58f2712a9e75e66a1ffba4460cc9a1d5",
        "3cb5e3b004cc224a538bc17078b651cbe5fa2509",
        "MIT",
    ),
    "https://github.com/github/gh-aw-threat-detection": (
        "09cc2eed706368655776af394eab7146629f905a",
        "88003d9a48ba64c31f67cea827fe5ff726c472cf",
        "MIT",
    ),
    "https://github.com/github/gh-aw-harness": (
        "75ed171c12321e0cf4249a732c9860386bdc46a0",
        "c0161d9c6f8a0b50aa00a34c0162b8ac3fbeb01d",
        "MIT",
    ),
    "https://github.com/open-webui/open-webui": (
        "01f4282f1ffe0d6212f58d3afbeae21fffd0c4be",
        "89b6c6e20fd32f8df36309d2ffffc7c9e6043522",
        "Open WebUI License",
    ),
    "https://github.com/Graphify-Labs/graphify": (
        "00efd6e7969837ae4a9f11d8d504dcd3b20b09df",
        "d1512b0250570474ee45b4169ba2d3b1b35376fa",
        "Apache-2.0 and MIT",
    ),
    "https://github.com/github/codeql": (
        "74c8994c9fa3ca4551c01879ef9f74e3e09e791a",
        "06e9890166e4ad4bd015439016a137f8f4099ca8",
        "MIT",
    ),
    "https://github.com/github/github-mcp-server": (
        "3778a41476e31a072430cfee7c5d31c5f72def60",
        "ae97fb877726d54334bebcaaa640e58bab3ca84e",
        "MIT",
    ),
    "https://github.com/github/branch-deploy": (
        "7ad5ec6a7e19e3e341846e4d33c4ed779b3e8036",
        "5a901697cd7671a61db70f7f27787c6ba257deb8",
        "MIT",
    ),
    "https://github.com/github/local-action": (
        "b9351d8a8f1e6eed27646f4d892b49a3847ba180",
        "77093c3aeb8b01bcc35f24c7160596b725a7421d",
        "MIT",
    ),
}


def test_upstream_reference_identities_match_docs_and_public_site_data() -> None:
    provenance = PROVENANCE.read_text(encoding="utf-8")
    site_data = SITE_DATA.read_text(encoding="utf-8")

    for repository, (commit, tree, license_name) in REFERENCES.items():
        for text in (provenance, site_data):
            assert repository in text
            assert commit in text
            assert tree in text
            assert license_name in text


def test_upstream_ledger_preserves_non_claim_and_acceptance_boundaries() -> None:
    provenance = PROVENANCE.read_text(encoding="utf-8")
    credits = (ROOT / "docs" / "CREDITS_AND_CONTRIBUTIONS.md").read_text(
        encoding="utf-8"
    )

    assert "POINTER_TEXT_ONLY" in provenance
    assert "copied no remote payload" in provenance
    assert "only a real GitHub session identifier" in provenance
    assert "advisory_only" in provenance
    assert "Open WebUI License" in provenance
    assert "does not move the accepted\nPV5 pointer" in provenance
    assert "UPSTREAM_REFERENCE_PROVENANCE.md" in credits
