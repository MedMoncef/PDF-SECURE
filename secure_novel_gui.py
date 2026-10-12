from __future__ import annotations

import queue
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import pymupdf

from secure_novel import (
    DPI,
    add_watermark,
    encrypt_pdf,
    rasterize_pdf,
    verify_output,
)


class SecureNovelApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Secure Novel PDF")
        self.geometry("760x560")
        self.minsize(680, 480)

        self.pdf_path = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.watermark_text = tk.StringVar(value="Licensed digital copy")
        self.progress_value = tk.DoubleVar(value=0)
        self.status = tk.StringVar(value="Choose a PDF to begin.")
        self.log_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.start_button: ttk.Button

        self._build_ui()
        self.after(100, self._drain_log_queue)

    def _build_ui(self) -> None:
        padding = {"padx": 12, "pady": 8}
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(5, weight=1)

        ttk.Label(frame, text="Source PDF:").grid(row=0, column=0, sticky="w", **padding)
        ttk.Entry(frame, textvariable=self.pdf_path).grid(
            row=0, column=1, sticky="ew", **padding
        )
        ttk.Button(frame, text="Pick PDF", command=self._pick_pdf).grid(
            row=0, column=2, **padding
        )

        ttk.Label(frame, text="Output folder:").grid(
            row=1, column=0, sticky="w", **padding
        )
        ttk.Entry(frame, textvariable=self.output_dir).grid(
            row=1, column=1, sticky="ew", **padding
        )
        ttk.Button(frame, text="Choose folder", command=self._pick_output_dir).grid(
            row=1, column=2, **padding
        )

        ttk.Label(frame, text="Watermark:").grid(
            row=2, column=0, sticky="w", **padding
        )
        ttk.Entry(frame, textvariable=self.watermark_text).grid(
            row=2, column=1, columnspan=2, sticky="ew", **padding
        )

        note = (
            "Creates gumroad_upload.pdf (flattened + watermarked) and "
            "final_secured_novel.pdf (AES-256 restricted)."
        )
        ttk.Label(frame, text=note, wraplength=680).grid(
            row=3, column=0, columnspan=3, sticky="w", **padding
        )

        self.progress = ttk.Progressbar(
            frame,
            variable=self.progress_value,
            maximum=100,
            mode="determinate",
        )
        self.progress.grid(row=4, column=0, columnspan=3, sticky="ew", **padding)

        self.log = tk.Text(frame, height=14, state="disabled", wrap="word")
        self.log.grid(row=5, column=0, columnspan=3, sticky="nsew", **padding)

        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=3, sticky="e", **padding)
        self.start_button = ttk.Button(
            buttons,
            text="Start",
            command=self._start,
        )
        self.start_button.pack(side="left", padx=6)
        self.cancel_button = ttk.Button(
            buttons,
            text="Cancel",
            command=self.cancel_event.set,
            state="disabled",
        )
        self.cancel_button.pack(side="left")
        ttk.Label(frame, textvariable=self.status).grid(
            row=7, column=0, columnspan=3, sticky="w", **padding
        )

    def _pick_pdf(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose the novel PDF",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if selected:
            self.pdf_path.set(selected)
            if not self.output_dir.get():
                self.output_dir.set(str(Path(selected).parent))

    def _pick_output_dir(self) -> None:
        selected = filedialog.askdirectory(title="Choose output folder")
        if selected:
            self.output_dir.set(selected)

    def _append_log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _start(self) -> None:
        input_path = Path(self.pdf_path.get())
        output_dir = Path(self.output_dir.get() or input_path.parent)

        if not input_path.is_file() or input_path.suffix.casefold() != ".pdf":
            messagebox.showerror("Invalid PDF", "Choose an existing PDF file.")
            return
        if not output_dir.is_dir():
            messagebox.showerror("Invalid output folder", "Choose an existing folder.")
            return
        if not self.watermark_text.get().strip():
            messagebox.showerror("Invalid watermark", "Enter watermark text.")
            return

        owner_password = simpledialog.askstring(
            "Owner password",
            "Enter a strong owner/permissions password:",
            show="*",
            parent=self,
        )
        if owner_password is None:
            return
        if len(owner_password) < 12:
            proceed = messagebox.askyesno(
                "Weak password",
                "Use at least 12 characters for the owner password. Continue anyway?",
                parent=self,
            )
            if not proceed:
                return
        confirmation = simpledialog.askstring(
            "Confirm password",
            "Confirm the owner/permissions password:",
            show="*",
            parent=self,
        )
        if owner_password != confirmation:
            messagebox.showerror("Password mismatch", "The passwords do not match.")
            return

        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress_value.set(0)
        self._append_log(f"Input: {input_path}")
        self._append_log(f"Output folder: {output_dir}")
        self.status.set("Working...")
        self.cancel_event.clear()
        self.worker = threading.Thread(
            target=self._run_worker,
            args=(input_path, output_dir, owner_password, self.watermark_text.get()),
            daemon=True,
        )
        self.worker.start()

    def _run_worker(
        self,
        input_path: Path,
        output_dir: Path,
        owner_password: str,
        watermark: str,
    ) -> None:
        gumroad_output = output_dir / "gumroad_upload.pdf"
        private_output = output_dir / "final_secured_novel.pdf"

        try:
            with tempfile.TemporaryDirectory(prefix="pdf_flatten_") as temp_dir:
                temp_path = Path(temp_dir)
                rasterized = temp_path / "rasterized.pdf"
                watermarked = temp_path / "watermarked.pdf"
                gumroad_flattened = temp_path / "gumroad_flattened.pdf"

                self._step("Phase 1: rasterizing pages", 5)
                page_count = rasterize_pdf(
                    input_path,
                    rasterized,
                    dpi=DPI,
                    progress_callback=lambda message: self._step(message, 5),
                )
                self._check_cancel()
                self._step("Adding watermark to every page", 45)
                add_watermark(rasterized, watermarked, watermark)
                self._check_cancel()
                self._step("Flattening watermarked pages", 55)
                rasterize_pdf(
                    watermarked,
                    gumroad_flattened,
                    dpi=DPI,
                    progress_callback=lambda message: self._step(message, 55),
                )
                gumroad_flattened.replace(gumroad_output)
                verify_output(gumroad_output, page_count, encrypted=False)
                self._check_cancel()
                self._step("Applying AES-256 permissions", 75)
                encrypt_pdf(rasterized, private_output, owner_password)
                verify_output(private_output, page_count, encrypted=True)

            self._step(f"Done: {page_count} pages processed", 100)
            self.log_queue.put(("success", (gumroad_output, private_output)))
        except Cancelled:
            self.log_queue.put(("cancelled", None))
        except Exception as exc:
            self.log_queue.put(("error", str(exc)))

    def _step(self, message: str, progress: float) -> None:
        self.log_queue.put(("log", message))
        self.log_queue.put(("progress", progress))

    def _check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise Cancelled()

    def _drain_log_queue(self) -> None:
        try:
            while True:
                kind, value = self.log_queue.get_nowait()
                if kind == "log":
                    self._append_log(str(value))
                elif kind == "progress":
                    self.progress_value.set(float(value))
                elif kind == "success":
                    gumroad, private = value
                    self._finish("Completed successfully.")
                    messagebox.showinfo(
                        "Finished",
                        f"Gumroad file:\n{gumroad}\n\nPrivate file:\n{private}",
                        parent=self,
                    )
                elif kind == "cancelled":
                    self._finish("Cancelled.")
                elif kind == "error":
                    self._finish("Failed.")
                    messagebox.showerror("Processing failed", str(value), parent=self)
        except queue.Empty:
            pass
        self.after(100, self._drain_log_queue)

    def _finish(self, status: str) -> None:
        self.status.set(status)
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")


class Cancelled(Exception):
    pass


if __name__ == "__main__":
    SecureNovelApp().mainloop()
