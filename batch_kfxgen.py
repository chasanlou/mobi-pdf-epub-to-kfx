import argparse
import json
import os
import time
from pathlib import Path

from main import convert_to_kfx


def split_metadata(base_name):
    if "-" not in base_name:
        return base_name, None
    title, author = base_name.split("-", 1)
    return title, author


def find_generated_kfx(output_dir, base, started):
    expected = output_dir / f"{base}.kfx"
    if expected.exists() and expected.stat().st_mtime >= started:
        return expected

    matches = [
        p for p in output_dir.glob("*.kfx")
        if p.stat().st_mtime >= started and (p.stem == base or p.stem.startswith(base))
    ]
    if matches:
        return max(matches, key=lambda p: p.stat().st_mtime)

    recent = [p for p in output_dir.glob("*.kfx") if p.stat().st_mtime >= started]
    if recent:
        return max(recent, key=lambda p: p.stat().st_mtime)
    raise FileNotFoundError(f"没有找到本次生成的 KFX：{base}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--page-progression", default="rtl", choices=("ltr", "rtl"))
    parser.add_argument("--layout-view", default="virtual", choices=("fixed", "virtual"))
    parser.add_argument("--virtual-panel-axis", default="vertical", choices=("vertical", "horizontal"))
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    jobs = json.loads(Path(args.manifest).read_text(encoding="utf-8-sig"))

    print(f"kckfxgen batch: {len(jobs)} files")
    done = []
    for index, job in enumerate(jobs, 1):
        base = job["base"]
        cbz = Path(job["cbz"])
        title, author = split_metadata(base)
        expected = output_dir / f"{base}.kfx"
        if expected.exists():
            expected.unlink()
            print(f"delete old KFX: {expected}")

        print(f"[{index}/{len(jobs)}] input: {cbz}")
        print(f"[{index}/{len(jobs)}] metadata: title=[{title}] author=[{author or ''}]")
        started = time.time()
        convert_to_kfx(
            cbz,
            output_dir,
            page_progression=args.page_progression,
            layout_view=args.layout_view,
            virtual_panel_axis=args.virtual_panel_axis,
            book_title=title,
            book_author=author,
            book_publisher=None,
        )
        generated = find_generated_kfx(output_dir, base, started)
        if generated.resolve() != expected.resolve():
            if expected.exists():
                expected.unlink()
            generated.replace(expected)
            print(f"[{index}/{len(jobs)}] rename: {generated} -> {expected}")
        print(f"[{index}/{len(jobs)}] done: {expected}")
        done.append(str(expected))

    print(f"kckfxgen batch complete: {len(done)} files")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    main()
