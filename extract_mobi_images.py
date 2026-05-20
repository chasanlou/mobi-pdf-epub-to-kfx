import glob
import multiprocessing
import os
import shutil
import sys
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
PDF_RENDER_SCALE = float(os.environ.get("MOBIKFX_PDF_SCALE", "2"))
PDF_MAX_WORKERS = int(os.environ.get("MOBIKFX_PDF_WORKERS", "0") or "0")

_PDF_DOC = None
_PDF_PATH = None
_PDF_OUTPUT_DIR = None
_PDF_SCALE = 2.0


def _pdf_worker_init(pdf_path, output_dir, scale):
    global _PDF_DOC, _PDF_PATH, _PDF_OUTPUT_DIR, _PDF_SCALE
    import fitz

    _PDF_PATH = pdf_path
    _PDF_OUTPUT_DIR = output_dir
    _PDF_SCALE = scale
    _PDF_DOC = fitz.open(pdf_path)


def _render_pdf_page(page_index):
    import fitz

    doc = _PDF_DOC
    if doc is None:
        doc = fitz.open(_PDF_PATH)
    page = doc.load_page(page_index)
    matrix = fitz.Matrix(_PDF_SCALE, _PDF_SCALE)
    pix = page.get_pixmap(matrix=matrix, alpha=False, annots=False)
    output_path = os.path.join(_PDF_OUTPUT_DIR, f"page_{page_index + 1:04d}.png")
    pix.save(output_path)
    return output_path


def extract_single_mobi(mobi_path, output_dir):
    import mobi

    tempdir = None
    try:
        tempdir, _ = mobi.extract(mobi_path)
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        mobi8_dir = os.path.join(tempdir, "mobi8")
        search_base = mobi8_dir if os.path.exists(mobi8_dir) else tempdir
        image_extensions = ["*.jpg", "*.jpeg", "*.png", "*.gif"]

        raw_images = []
        for ext in image_extensions:
            search_path = os.path.join(search_base, "**", ext)
            raw_images.extend(glob.glob(search_path, recursive=True))

        unique_images = {}
        for img_path in raw_images:
            fname = os.path.basename(img_path)
            if fname not in unique_images or os.path.getsize(img_path) > os.path.getsize(unique_images[fname]):
                unique_images[fname] = img_path

        final_images = sorted(unique_images.values())
        if not final_images:
            raise RuntimeError("No images found in extracted MOBI content.")

        for index, img_path in enumerate(final_images, 1):
            ext = os.path.splitext(img_path)[1]
            new_filename = f"image_{index:04d}{ext}"
            dest_path = os.path.join(output_dir, new_filename)
            shutil.copy2(img_path, dest_path)

        return len(final_images)
    finally:
        if tempdir and os.path.exists(tempdir):
            shutil.rmtree(tempdir, ignore_errors=True)


def reset_output_dir(output_dir):
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir, exist_ok=True)


def extract_single_epub(epub_path, output_dir):
    reset_output_dir(output_dir)
    copied = 0
    with zipfile.ZipFile(epub_path) as zf:
        names = [
            name for name in zf.namelist()
            if not name.endswith("/") and Path(name).suffix.lower() in IMAGE_EXTENSIONS
        ]
        names.sort()
        for index, name in enumerate(names, 1):
            ext = Path(name).suffix.lower()
            dest_path = os.path.join(output_dir, f"image_{index:04d}{ext}")
            with zf.open(name) as src, open(dest_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            copied += 1
    if copied == 0:
        raise RuntimeError("No images found in EPUB content.")
    return copied


def extract_single_pdf(pdf_path, output_dir):
    try:
        import fitz
    except Exception as exc:
        raise RuntimeError("PDF 提取需要 PyMuPDF(fitz)，当前 Python 环境没有这个组件。") from exc

    reset_output_dir(output_dir)
    doc = fitz.open(pdf_path)
    try:
        page_count = doc.page_count
        if page_count == 0:
            raise RuntimeError("PDF has no pages.")
    finally:
        doc.close()

    cpu_count = os.cpu_count() or 1
    workers = PDF_MAX_WORKERS or max(1, min(cpu_count - 1 if cpu_count > 1 else 1, 4))
    workers = max(1, min(workers, page_count))

    if workers == 1 or page_count < 4:
        _pdf_worker_init(pdf_path, output_dir, PDF_RENDER_SCALE)
        try:
            for page_index in range(page_count):
                _render_pdf_page(page_index)
        finally:
            if _PDF_DOC is not None:
                _PDF_DOC.close()
        return page_count

    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=workers,
        mp_context=context,
        initializer=_pdf_worker_init,
        initargs=(pdf_path, output_dir, PDF_RENDER_SCALE),
    ) as executor:
        futures = [executor.submit(_render_pdf_page, page_index) for page_index in range(page_count)]
        for future in as_completed(futures):
            future.result()
    return page_count


def extract_book_images(book_path, output_dir):
    suffix = Path(book_path).suffix.lower()
    if suffix == ".mobi":
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir)
        return extract_single_mobi(book_path, output_dir)
    if suffix == ".epub":
        return extract_single_epub(book_path, output_dir)
    if suffix == ".pdf":
        return extract_single_pdf(book_path, output_dir)
    raise RuntimeError(f"Unsupported input type: {suffix}")


def main():
    if len(sys.argv) != 3:
        print("Usage: extract_mobi_images.py <book_path> <output_dir>", file=sys.stderr)
        return 2

    book_path = sys.argv[1]
    output_dir = sys.argv[2]
    count = extract_book_images(book_path, output_dir)
    print(f"Extracted {count} images to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
