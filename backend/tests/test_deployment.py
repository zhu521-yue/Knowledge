from pathlib import Path


def test_container_disables_unredacted_uvicorn_access_log() -> None:
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")

    assert '"--no-access-log"' in dockerfile