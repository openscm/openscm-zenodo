"""
Tests of `openscm_zenodo.zenodo`
"""

from __future__ import annotations

from openscm_zenodo.zenodo import ZenodoInteractor


def test_token_hidden():
    zi = ZenodoInteractor(token="special")  # noqa: S106

    assert "special" not in str(zi)
    assert "***" in str(zi)
    assert "special" not in repr(zi)
    assert "***" in repr(zi)
