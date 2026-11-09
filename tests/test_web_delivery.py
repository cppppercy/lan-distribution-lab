from __future__ import annotations

from fastapi.testclient import TestClient

from lan_distribution_lab.app import create_app
from lan_distribution_lab.config import Settings


def test_built_web_files_are_served_without_shadowing_api(tmp_path) -> None:
    web_dist = tmp_path / "dist"
    web_dist.mkdir()
    (web_dist / "index.html").write_text("<main>LAN web</main>", encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "state", managed_root=tmp_path / "managed")
    client = TestClient(create_app(settings, web_dist_dir=web_dist))

    page = client.get("/")
    health = client.get("/api/health")

    assert page.status_code == 200
    assert "LAN web" in page.text
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
