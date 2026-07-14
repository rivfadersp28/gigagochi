from __future__ import annotations

import argparse
import os
from pathlib import Path

from app.services.image_service import make_character_foreground_image_bytes


def migrate(root: Path, *, asset_id: str | None = None, force: bool = False) -> int:
    search_root = root / asset_id if asset_id else root
    processed = 0
    for source_path in sorted(search_root.glob("*-character.png" if asset_id else "*/*-character.png")):
        target_path = source_path.with_name(source_path.name.replace("-character.png", "-foreground.png"))
        if target_path.exists() and not force:
            continue
        temporary_path = target_path.with_suffix(".tmp")
        temporary_path.write_bytes(make_character_foreground_image_bytes(source_path.read_bytes()))
        os.replace(temporary_path, target_path)
        processed += 1
        print(target_path.relative_to(root))
    return processed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create transparent foreground PNG files for generated pets.",
    )
    parser.add_argument("root", type=Path)
    parser.add_argument("--asset-id")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(f"processed={migrate(args.root, asset_id=args.asset_id, force=args.force)}")


if __name__ == "__main__":
    main()
