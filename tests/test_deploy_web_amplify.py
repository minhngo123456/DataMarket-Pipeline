import io
import zipfile

import pytest

from scripts.deploy_web_amplify import zip_dist


def test_zip_has_index_at_root_and_forward_slashes(tmp_path):
    (tmp_path / "index.html").write_text("<html>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("1")
    names = zipfile.ZipFile(io.BytesIO(zip_dist(tmp_path))).namelist()
    assert sorted(names) == ["assets/app.js", "index.html"]


def test_zip_requires_a_build(tmp_path):
    with pytest.raises(FileNotFoundError):
        zip_dist(tmp_path)
