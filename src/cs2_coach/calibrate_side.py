"""Save a side emblem from a game-area screenshot with a known team."""
import argparse
from pathlib import Path

from .context import SideReader


def main():
    import cv2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--side", choices=("CT", "T"), required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("config/templates"))
    args = parser.parse_args()
    frame = cv2.imread(str(args.image))
    if frame is None:
        parser.error("Cannot read screenshot")
    crop = SideReader(cv2, {}, args.output_dir).crop(frame)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / f"{args.side}.png"
    if not cv2.imwrite(str(path), crop):
        raise OSError(f"Cannot save {path}")
    print(path)


if __name__ == "__main__":
    main()
