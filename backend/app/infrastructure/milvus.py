from urllib.error import URLError
from urllib.request import urlopen

from app.config import Settings


class MilvusUnavailable(RuntimeError):
    pass


def check_milvus_health(settings: Settings) -> None:
    if not settings.milvus_health_url:
        return

    try:
        with urlopen(
            settings.milvus_health_url,
            timeout=settings.milvus_health_timeout_seconds,
        ) as response:
            if response.status != 200:
                raise MilvusUnavailable(f"unexpected_status:{response.status}")
    except (OSError, URLError) as exc:
        raise MilvusUnavailable(settings.milvus_health_url) from exc
