from pathlib import Path

from app.config import Settings


class StorageUnavailable(RuntimeError):
    pass


def iter_required_storage_paths(settings: Settings) -> tuple[Path, ...]:
    return (
        settings.storage_notes_path,
        settings.storage_uploads_path,
        settings.storage_raw_path,
        settings.storage_parsed_path,
        settings.storage_exports_path,
        settings.storage_cache_path,
    )


def check_storage_paths(settings: Settings) -> None:
    for path in iter_required_storage_paths(settings):
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".knowledge-write-check"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except OSError as exc:
            raise StorageUnavailable(str(path)) from exc
