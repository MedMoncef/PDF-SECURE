from __future__ import annotations

import inspect
import sys
import tempfile
from getpass import getpass
from pathlib import Path

import pymupdf
from pypdf import PdfReader, PdfWriter
from pypdf.constants import UserAccessPermissions


INPUT_FILE = Path("input.pdf")
OUTPUT_FILE = Path("final_secured_novel.pdf")
GUMROAD_OUTPUT_FILE = Path("gumroad_upload.pdf")
WATERMARK_TEXT = "Licensed digital copy"
DPI = 300


def rasterize_pdf(
    input_path: Path,
    output_path: Path,
    dpi: int = 300,
    progress_callback=None,
) -> int:
    """
    Convert each PDF page into a high-resolution PNG image and place each
    image on a new PDF page. The resulting PDF contains no text layer.
    """
    source_pdf = pymupdf.open(input_path)
    flattened_pdf = pymupdf.open()

    try:
        scale = dpi / 72.0
        matrix = pymupdf.Matrix(scale, scale)

        for page_number, source_page in enumerate(source_pdf, start=1):
            message = (
                f"Rasterizing page {page_number}/{source_pdf.page_count} "
                f"at {dpi} DPI..."
            )
            if progress_callback is None:
                print(message)
            else:
                progress_callback(
                    message,
                    page_number,
                    source_pdf.page_count,
                )

            # Render the page as an RGB image without transparency.
            pixmap = source_page.get_pixmap(
                matrix=matrix,
                colorspace=pymupdf.csRGB,
                alpha=False,
            )

            # Preserve the original physical page size in points.
            output_page = flattened_pdf.new_page(
                width=source_page.rect.width,
                height=source_page.rect.height,
            )

            # PNG is lossless and preserves text edges better than JPEG.
            png_bytes = pixmap.tobytes("png")

            output_page.insert_image(
                output_page.rect,
                stream=png_bytes,
                keep_proportion=False,
            )

        flattened_pdf.save(output_path, garbage=4, deflate=True)
        return source_pdf.page_count

    finally:
        flattened_pdf.close()
        source_pdf.close()


def add_watermark(
    input_path: Path,
    output_path: Path,
    watermark_text: str,
) -> None:
    """Add a visible watermark before the document is flattened again."""
    document = pymupdf.open(input_path)

    try:
        for page in document:
            page.insert_text(
                pymupdf.Point(page.rect.width / 2, page.rect.height / 2),
                watermark_text,
                fontsize=24,
                fontname="helv",
                color=(0.45, 0.45, 0.45),
                fill_opacity=0.28,
            )

        document.save(output_path, garbage=4, deflate=True)
    finally:
        document.close()


def encrypt_pdf(
    input_path: Path,
    output_path: Path,
    owner_password: str,
) -> None:
    """
    Encrypt the PDF with a blank user/open password and a private owner
    password. No user permissions are granted.
    """
    reader = PdfReader(input_path)
    writer = PdfWriter()

    for page in reader.pages:
        writer.add_page(page)

    # A zero-value permission set disables printing, copying/extraction,
    # annotations, form filling, document modification, and accessibility
    # extraction permissions.
    no_permissions = UserAccessPermissions(0)

    encrypt_parameters = {
        "user_password": "",
        "owner_password": owner_password,
        "algorithm": "AES-256",
    }

    # pypdf has used both names across releases. This keeps the script
    # compatible with supported pypdf versions while still using AES-256.
    encrypt_signature = inspect.signature(writer.encrypt)

    if "permissions_flag" in encrypt_signature.parameters:
        encrypt_parameters["permissions_flag"] = no_permissions
    elif "permissions" in encrypt_signature.parameters:
        encrypt_parameters["permissions"] = no_permissions
    else:
        raise RuntimeError(
            "This pypdf version does not expose a permissions parameter. "
            'Upgrade it with: py -m pip install --upgrade "pypdf[crypto]"'
        )

    writer.encrypt(**encrypt_parameters)

    with output_path.open("wb") as output_stream:
        writer.write(output_stream)


def verify_output(
    output_path: Path,
    expected_page_count: int,
    *,
    encrypted: bool,
) -> None:
    """Perform basic checks on a generated PDF."""
    secured_reader = PdfReader(output_path)

    if secured_reader.is_encrypted != encrypted:
        expected = "encrypted" if encrypted else "unencrypted"
        raise RuntimeError(f"{output_path} is not {expected} as expected.")

    if len(secured_reader.pages) != expected_page_count:
        raise RuntimeError(
            "The output PDF page count does not match the input PDF."
        )

    if not encrypted:
        if secured_reader.pages[0].extract_text():
            raise RuntimeError(
                f"{output_path} still contains an extractable text layer."
            )


def main() -> int:
    if not INPUT_FILE.exists():
        print(f"Error: Could not find {INPUT_FILE.resolve()}", file=sys.stderr)
        return 1

    if not INPUT_FILE.is_file():
        print(f"Error: {INPUT_FILE} is not a regular file.", file=sys.stderr)
        return 1

    owner_password = getpass(
        "Enter a strong owner/permissions password: "
    )

    if not owner_password:
        print(
            "Error: The owner password cannot be blank.",
            file=sys.stderr,
        )
        return 1

    owner_password_confirmation = getpass(
        "Confirm the owner/permissions password: "
    )

    if owner_password != owner_password_confirmation:
        print("Error: The passwords do not match.", file=sys.stderr)
        return 1

    if len(owner_password) < 12:
        print(
            "Warning: Use an owner password of at least 12 characters, "
            "preferably a randomly generated passphrase.",
            file=sys.stderr,
        )

    try:
        with tempfile.TemporaryDirectory(prefix="pdf_flatten_") as temp_dir:
            rasterized_path = Path(temp_dir) / "rasterized.pdf"
            watermarked_path = Path(temp_dir) / "watermarked.pdf"
            gumroad_flattened_path = Path(temp_dir) / "gumroad_flattened.pdf"

            print("\nPhase 1: Rasterization and flattening")
            page_count = rasterize_pdf(
                INPUT_FILE,
                rasterized_path,
                dpi=DPI,
            )

            print("\nCreating Gumroad upload PDF")
            add_watermark(
                rasterized_path,
                watermarked_path,
                WATERMARK_TEXT,
            )
            rasterize_pdf(
                watermarked_path,
                gumroad_flattened_path,
                dpi=DPI,
            )
            gumroad_flattened_path.replace(GUMROAD_OUTPUT_FILE)
            verify_output(
                GUMROAD_OUTPUT_FILE,
                page_count,
                encrypted=False,
            )

            print("\nPhase 2: AES-256 encryption and permissions")
            encrypt_pdf(
                rasterized_path,
                OUTPUT_FILE,
                owner_password,
            )

        verify_output(OUTPUT_FILE, page_count, encrypted=True)

        print("\nCompleted successfully.")
        print(f"Input:  {INPUT_FILE.resolve()}")
        print(f"Gumroad: {GUMROAD_OUTPUT_FILE.resolve()}")
        print(f"Private: {OUTPUT_FILE.resolve()}")
        print(f"Pages:  {page_count}")
        print("Gumroad PDF: unencrypted, flattened, watermarked")
        print("Private PDF: AES-256, blank user password, restricted permissions")

        return 0

    except Exception as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())