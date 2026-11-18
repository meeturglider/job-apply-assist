from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.responses import FileResponse, Response


class ArtifactStore:
    def write(self, application_id: str, name: str, data: str | bytes) -> None:
        raise NotImplementedError

    def read_text(self, application_id: str, name: str) -> str:
        raise NotImplementedError

    def read_bytes(self, application_id: str, name: str) -> bytes:
        raise NotImplementedError

    def exists(self, application_id: str, name: str) -> bool:
        raise NotImplementedError

    def delete(self, application_id: str, name: str) -> None:
        raise NotImplementedError

    def size(self, application_id: str, name: str) -> int:
        raise NotImplementedError

    @contextmanager
    def render_dir(self, application_id: str) -> Iterator[Path]:
        raise NotImplementedError
        yield Path()

    def response(
        self,
        application_id: str,
        filename: str,
        media_type: str,
        disposition: str,
    ) -> Response:
        raise NotImplementedError


class FileArtifactStore(ArtifactStore):
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, application_id: str, name: str) -> Path:
        path = self.root / application_id / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def path(self, application_id: str, name: str) -> Path:
        return self._path(application_id, name)

    def write(self, application_id: str, name: str, data: str | bytes) -> None:
        path = self._path(application_id, name)
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            path.write_text(data, encoding="utf-8")

    def read_text(self, application_id: str, name: str) -> str:
        return self._path(application_id, name).read_text(encoding="utf-8")

    def read_bytes(self, application_id: str, name: str) -> bytes:
        return self._path(application_id, name).read_bytes()

    def exists(self, application_id: str, name: str) -> bool:
        return self._path(application_id, name).exists()

    def delete(self, application_id: str, name: str) -> None:
        self._path(application_id, name).unlink(missing_ok=True)

    def size(self, application_id: str, name: str) -> int:
        path = self._path(application_id, name)
        return path.stat().st_size if path.exists() else 0

    @contextmanager
    def render_dir(self, application_id: str) -> Iterator[Path]:
        directory = self.root / application_id
        directory.mkdir(parents=True, exist_ok=True)
        yield directory

    def response(
        self,
        application_id: str,
        filename: str,
        media_type: str,
        disposition: str,
    ) -> Response:
        path = self._path(application_id, filename)
        return FileResponse(
            path,
            media_type=media_type,
            filename=path.name,
            content_disposition_type=disposition,
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
        )


class MemoryArtifactStore(ArtifactStore):
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], bytes] = {}

    def write(self, application_id: str, name: str, data: str | bytes) -> None:
        payload = data.encode("utf-8") if isinstance(data, str) else data
        self._items[(application_id, name)] = payload

    def read_text(self, application_id: str, name: str) -> str:
        return self.read_bytes(application_id, name).decode("utf-8")

    def read_bytes(self, application_id: str, name: str) -> bytes:
        try:
            return self._items[(application_id, name)]
        except KeyError as exc:
            raise FileNotFoundError(f"Artifact not found: {name}") from exc

    def exists(self, application_id: str, name: str) -> bool:
        return (application_id, name) in self._items

    def delete(self, application_id: str, name: str) -> None:
        self._items.pop((application_id, name), None)

    def size(self, application_id: str, name: str) -> int:
        item = self._items.get((application_id, name))
        return len(item) if item is not None else 0

    @contextmanager
    def render_dir(self, application_id: str) -> Iterator[Path]:
        with TemporaryDirectory(prefix="job-assist-render-") as directory:
            yield Path(directory)

    def response(
        self,
        application_id: str,
        filename: str,
        media_type: str,
        disposition: str,
    ) -> Response:
        payload = self.read_bytes(application_id, filename)
        return Response(
            content=payload,
            media_type=media_type,
            headers={
                "Content-Disposition": f'{disposition}; filename="{filename}"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            },
        )
