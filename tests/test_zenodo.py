"""
Tests of `openscm_zenodo.zenodo`

**TO BE DELETED.** Everything here covers `ZenodoInteractor`, the legacy deposit
API client, which goes when the CLI is trimmed (plan Part 8). Delete this file
with it — `ZenodoClient`'s equivalent of this test is
`tests/test_client.py`.
"""

from __future__ import annotations

from openscm_zenodo.zenodo import ZenodoInteractor


def test_token_hidden():
    zi = ZenodoInteractor(token="special")  # noqa: S106

    assert "special" not in str(zi)
    assert "***" in str(zi)
    assert "special" not in repr(zi)
    assert "***" in repr(zi)
