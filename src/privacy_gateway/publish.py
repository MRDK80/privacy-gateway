"""Публикация артефакта из временного файла — #64, ADR-64.

Единый publication primitive для обоих writer paths
(``pipeline._write_atomic`` и ``manifest.save_manifest``).

``overwrite=True``  — прежняя семантика: ``os.replace``.
``overwrite=False`` — no-clobber публикация без fallback:
    POSIX   ``os.link(tmp, target)`` + best-effort ``os.unlink(tmp)``;
    Windows ``os.rename(tmp, target)``.

POSIX ``link()`` атомарно создаёт новую directory entry и отказывает с
EEXIST, если целевое имя занято, включая symlink и broken symlink. На
Windows ``os.rename()`` при существующем ``dst`` всегда поднимает
``FileExistsError``; ``os.link()`` там не используется, поскольку
``CreateHardLink`` поддерживается только на NTFS и только для файлов,
из-за чего единый примитив отказывал бы на FAT32 и ReFS 3.4.

Fail closed: при недоступности primitive операция завершается явной
ошибкой; откат на ``os.replace()`` запрещён, иначе гарантия исчезает
молча.

Граница гарантии узкая: не заменяется path entry, существующая в момент
атомарного создания целевого имени. Crash durability, ``fsync`` и
транзакционность набора артефактов не обещаются.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["publish_temp"]


def publish_temp(
    tmp_name: str | os.PathLike[str],
    target: Path,
    *,
    overwrite: bool,
) -> None:
    """Опубликовать *tmp_name* по пути *target*.

    Временный файл обязан быть полностью записан, закрыт и находиться в
    каталоге назначения: hard link требует одной файловой системы.

    Args:
        tmp_name:  Путь к закрытому временному файлу.
        target:    Итоговый путь публикации.
        overwrite: Разрешена ли замена существующего path entry.

    Raises:
        FileExistsError: ``overwrite=False`` и целевое имя занято.
        OSError:         Отказ публикации, включая ``EPERM`` на файловых
                         системах без поддержки hard links и
                         ``PermissionError`` при sharing violation.
    """
    if overwrite:
        os.replace(tmp_name, target)
        return

    if os.name == "nt":
        # На Windows rename не заменяет существующий dst.
        os.rename(tmp_name, target)
        return

    # POSIX: атомарное создание имени, EEXIST при занятом target.
    os.link(tmp_name, target)
    try:
        os.unlink(tmp_name)
    except OSError:
        # Публикация уже состоялась: отказ cleanup её не отменяет.
        pass
