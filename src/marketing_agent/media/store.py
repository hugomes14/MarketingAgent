"""Bytes imutaveis por marca e SHA-256, publicados atomicamente no disco local.

O chamador normaliza e valida a imagem antes de guardar. Este modulo so conhece bytes;
nao depende de Pillow, da base de dados ou da configuracao da aplicacao.
"""

import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

_BRAND = r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}"
_KEY = re.compile(rf"({_BRAND})/([0-9a-f]{{2}})/([0-9a-f]{{64}})\.(jpg|png)")


@contextmanager
def _directory(
    path: str | Path, *, parent_fd: int | None = None, create: bool = False
) -> Iterator[int]:
    if create:
        with suppress(FileExistsError):
            os.mkdir(path, mode=0o700, dir_fd=parent_fd)
    # Descritores relativos e O_NOFOLLOW impedem trocas de symlinks entre validar e abrir.
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def _read_at(directory_fd: int, name: str) -> bytes:
    # O_NONBLOCK evita bloquear se um objeto tiver sido substituido por um FIFO.
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise MediaStoreError("O objeto de media não é um ficheiro regular.")
        return stream.read()


def _publish(directory_fd: int, name: str, data: bytes) -> None:
    temporary = f".upload-{uuid4().hex}"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        mode=0o600,
        dir_fd=directory_fd,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # Nunca usar replace/rename: uma corrida nao pode sobrescrever o objeto anterior.
            os.link(
                temporary,
                name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            if _read_at(directory_fd, name) != data:
                raise MediaStoreError("A chave de media já contém bytes diferentes.") from None
        os.fsync(directory_fd)
    finally:
        os.unlink(temporary, dir_fd=directory_fd)


class MediaStoreError(Exception):
    """Uma chave e invalida ou o armazenamento nao consegue preservar os bytes."""


@dataclass(frozen=True)
class StoredObject:
    """Referencia relativa ao armazenamento, resumo SHA-256 e tamanho em bytes."""

    key: str
    sha256: str
    size: int


class MediaStore(Protocol):
    """Fronteira de armazenamento: bytes imutaveis e chaves relativas opacas."""

    def put(self, brand_id: str, data: bytes, extension: Literal["jpg", "png"]) -> StoredObject:
        """Guarda bytes sem sobrescrever e devolve a sua referencia."""
        ...

    def read(self, key: str) -> bytes:
        """Le os bytes de uma chave emitida por put, ou levanta MediaStoreError."""
        ...


class LocalMediaStore:
    """Armazenamento privado local, com publicacao por hard link sem sobrescrita."""

    def __init__(self, root: Path) -> None:
        """Prepara a raiz privada; os antepassados da raiz sao configuracao confiavel."""
        self._root = root.absolute()
        try:
            self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as error:
            raise MediaStoreError("Não foi possível preparar a pasta de media.") from error

    def put(self, brand_id: str, data: bytes, extension: Literal["jpg", "png"]) -> StoredObject:
        """Guarda os bytes uma vez e recusa uma chave ocupada por conteudo diferente."""
        if not re.fullmatch(_BRAND, brand_id) or extension not in ("jpg", "png"):
            raise MediaStoreError("Marca ou extensão de media inválida.")
        digest = sha256(data).hexdigest()
        name = f"{digest}.{extension}"
        key = f"{brand_id}/{digest[:2]}/{name}"
        try:
            with self._object_directory(brand_id, digest[:2], create=True) as directory_fd:
                _publish(directory_fd, name, data)
        except OSError as error:
            raise MediaStoreError("Não foi possível guardar o objeto de media.") from error
        return StoredObject(key=key, sha256=digest, size=len(data))

    def read(self, key: str) -> bytes:
        """Le um objeto sem permitir caminhos absolutos, travessia ou symlinks."""
        match = _KEY.fullmatch(key)
        if match is None or match[2] != match[3][:2]:
            raise MediaStoreError("Chave de media inválida.")
        try:
            with self._object_directory(match[1], match[2], create=False) as directory_fd:
                return _read_at(directory_fd, f"{match[3]}.{match[4]}")
        except OSError as error:
            raise MediaStoreError("Não foi possível ler o objeto de media.") from error

    @contextmanager
    def _object_directory(self, brand: str, prefix: str, *, create: bool) -> Iterator[int]:
        with (
            _directory(self._root) as root_fd,
            _directory(brand, parent_fd=root_fd, create=create) as brand_fd,
            _directory(prefix, parent_fd=brand_fd, create=create) as prefix_fd,
        ):
            yield prefix_fd
