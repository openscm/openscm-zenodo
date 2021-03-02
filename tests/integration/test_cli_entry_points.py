import pytest


@pytest.mark.script_launch_mode("subprocess")
@pytest.mark.parametrize(
    "entry_point",
    (
        ["openscm-zenodo"],
        ["openscm-zenodo", "create-new-version"],  # create new version
        ["openscm-zenodo", "upload"],  # upload files (using output from create new version)
        ["openscm-zenodo", "get-bucket"],  # get bucket for a given Zenodo record (which can then be used by upload)
    ),
)
def test_entry_points(entry_point, script_runner):
    res = script_runner.run(*entry_point, "--help")
    assert res.success
    assert res.stdout
    assert res.stderr == ""
