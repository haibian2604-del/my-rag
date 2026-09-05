"""SPA 托管测试：用临时静态目录验证回退逻辑。"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.spa import mount_spa


def make_client(tmp_path: Path) -> TestClient:
    (tmp_path / "index.html").write_text("<html>index</html>", encoding="utf-8")
    (tmp_path / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log(1)", encoding="utf-8")
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    mount_spa(app, tmp_path)
    return TestClient(app)


def test_index_fallback(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/").text == "<html>index</html>"
    assert client.get("/some/client/route").text == "<html>index</html>"


def test_static_file_served(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/favicon.svg").text == "<svg/>"
    assert client.get("/assets/app.js").text == "console.log(1)"


def test_unknown_api_returns_404_json(tmp_path):
    client = make_client(tmp_path)
    resp = client.get("/api/nope")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "接口不存在"}


def test_known_api_still_works(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/api/health").json() == {"status": "ok"}


def test_mount_skipped_without_static(tmp_path):
    # 空目录（无 index.html）时不挂载，行为等同纯 API 应用
    app = FastAPI()
    mount_spa(app, tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/").status_code == 404
