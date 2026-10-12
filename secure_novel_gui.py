from __future__ import annotations

import multiprocessing
import queue
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from SECURITY.create_epub import build_epub
from secure_novel import (
    DPI,
    add_watermark,
    encrypt_pdf,
    rasterize_pdf,
    verify_output,
)


class Cancelled(Exception):
    pass


def run_pdf_job(input_path, output_dir, owner_password, watermark, cancel_event, log_queue):
    def report(message, progress):
        if cancel_event.is_set():
            raise Cancelled()
        log_queue.put(("log", message))
        log_queue.put(("progress", progress))

    try:
        with tempfile.TemporaryDirectory(prefix="pdf_flatten_") as temp_dir:
            temp = Path(temp_dir)
            rasterized = temp / "rasterized.pdf"
            watermarked = temp / "watermarked.pdf"
            gumroad = temp / "gumroad_flattened.pdf"
            report("Phase 1: starting rasterization...", 1)
            pages = rasterize_pdf(
                input_path, rasterized, DPI,
                lambda m, p, t: report(m, 5 + 35 * p / t),
            )
            report("Watermarking every page...", 45)
            add_watermark(rasterized, watermarked, watermark)
            report("Phase 2: starting Gumroad flattening...", 55)
            rasterize_pdf(
                watermarked, gumroad, DPI,
                lambda m, p, t: report(m, 55 + 20 * p / t),
            )
            report("Saving and validating Gumroad PDF...", 76)
            gumroad_output = output_dir / "gumroad_upload.pdf"
            gumroad.replace(gumroad_output)
            verify_output(gumroad_output, pages, encrypted=False)
            report("Applying AES-256 permissions...", 82)
            private_output = output_dir / "final_secured_novel.pdf"
            encrypt_pdf(rasterized, private_output, owner_password)
            verify_output(private_output, pages, encrypted=True)
        report(f"Done: {pages} pages processed.", 100)
        log_queue.put(("success", (str(gumroad_output), str(private_output))))
    except Cancelled:
        log_queue.put(("cancelled", None))
    except Exception as exc:
        log_queue.put(("error", str(exc)))


def run_epub_job(input_path, output_dir, title, author, language, cancel_event, log_queue):
    try:
        log_queue.put(("log", "Reading Word manuscript and detecting chapters..."))
        if cancel_event.is_set():
            raise Cancelled()
        output = output_dir / f"{input_path.stem}.epub"
        chapters = build_epub(input_path, output, title, author, language)
        log_queue.put(("log", f"Created {chapters} EPUB chapters."))
        log_queue.put(("progress", 100))
        log_queue.put(("success", (str(output),)))
    except Cancelled:
        log_queue.put(("cancelled", None))
    except Exception as exc:
        log_queue.put(("error", str(exc)))


class SecureNovelApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Novel PDF / EPUB Tool")
        self.geometry("780x620")
        self.minsize(700, 520)
        self.mode = tk.StringVar(value="PDF")
        self.source = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.watermark = tk.StringVar(value="Licensed digital copy")
        self.title_text = tk.StringVar()
        self.author = tk.StringVar()
        self.language = tk.StringVar(value="en")
        self.status = tk.StringVar(value="Choose a file to begin.")
        self.progress_value = tk.DoubleVar()
        self.log_queue = multiprocessing.Queue()
        self.cancel_event = multiprocessing.Event()
        self.worker = None
        self._build_ui()
        self.after(100, self._drain_queue)

    def _build_ui(self):
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(7, weight=1)
        pad = {"padx": 8, "pady": 6}
        ttk.Label(frame, text="Conversion:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Radiobutton(frame, text="PDF security", variable=self.mode, value="PDF",
                        command=self._update_mode).grid(row=0, column=1, sticky="w", **pad)
        ttk.Radiobutton(frame, text="Word to EPUB", variable=self.mode, value="EPUB",
                        command=self._update_mode).grid(row=0, column=2, sticky="w", **pad)
        self.source_label = ttk.Label(frame, text="Source PDF:")
        self.source_label.grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(frame, textvariable=self.source).grid(row=1, column=1, sticky="ew", **pad)
        self.pick = ttk.Button(frame, text="Pick PDF", command=self._pick_source)
        self.pick.grid(row=1, column=2, **pad)
        ttk.Label(frame, text="Output folder:").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(frame, textvariable=self.output_dir).grid(row=2, column=1, sticky="ew", **pad)
        ttk.Button(frame, text="Choose folder", command=self._pick_output).grid(row=2, column=2, **pad)
        self.watermark_label = ttk.Label(frame, text="Watermark:")
        self.watermark_label.grid(row=3, column=0, sticky="w", **pad)
        self.watermark_entry = ttk.Entry(frame, textvariable=self.watermark)
        self.watermark_entry.grid(row=3, column=1, columnspan=2, sticky="ew", **pad)
        self.epub_widgets = []
        for row, label, variable in (
            (3, "Book title:", self.title_text),
            (4, "Author:", self.author),
            (5, "Language:", self.language),
        ):
            lab = ttk.Label(frame, text=label)
            ent = ttk.Entry(frame, textvariable=variable)
            self.epub_widgets.append((lab, ent, row))
        self.note = ttk.Label(frame, wraplength=700)
        self.note.grid(row=6, column=0, columnspan=3, sticky="w", **pad)
        self.progress = ttk.Progressbar(frame, variable=self.progress_value, maximum=100)
        self.progress.grid(row=7, column=0, columnspan=3, sticky="ew", **pad)
        self.log = tk.Text(frame, height=14, state="disabled", wrap="word")
        self.log.grid(row=8, column=0, columnspan=3, sticky="nsew", **pad)
        buttons = ttk.Frame(frame)
        buttons.grid(row=9, column=0, columnspan=3, sticky="e", **pad)
        self.start_button = ttk.Button(buttons, text="Start", command=self._start)
        self.start_button.pack(side="left", padx=6)
        self.cancel_button = ttk.Button(buttons, text="Cancel", command=self.cancel_event.set, state="disabled")
        self.cancel_button.pack(side="left")
        ttk.Label(frame, textvariable=self.status).grid(row=10, column=0, columnspan=3, sticky="w", **pad)
        self._update_mode()

    def _update_mode(self):
        epub_mode = self.mode.get() == "EPUB"
        self.source_label.configure(text="Source Word file:" if epub_mode else "Source PDF:")
        self.pick.configure(text="Pick DOCX" if epub_mode else "Pick PDF")
        self.note.configure(text=(
            "Creates a reflowable EPUB with chapter navigation."
            if epub_mode else
            "Creates gumroad_upload.pdf (flattened + watermarked) and "
            "final_secured_novel.pdf (AES-256 restricted)."
        ))
        if epub_mode:
            self.watermark_label.grid_remove()
            self.watermark_entry.grid_remove()
            for lab, ent, row in self.epub_widgets:
                lab.grid(row=row, column=0, sticky="w", padx=8, pady=6)
                ent.grid(row=row, column=1, columnspan=2, sticky="ew", padx=8, pady=6)
        else:
            self.watermark_label.grid()
            self.watermark_entry.grid()
            for lab, ent, _ in self.epub_widgets:
                lab.grid_remove()
                ent.grid_remove()

    def _pick_source(self):
        epub_mode = self.mode.get() == "EPUB"
        path = filedialog.askopenfilename(
            title="Choose Word manuscript" if epub_mode else "Choose PDF",
            filetypes=[("Word files", "*.docx")] if epub_mode else [("PDF files", "*.pdf")],
        )
        if path:
            self.source.set(path)
            if not self.output_dir.get():
                self.output_dir.set(str(Path(path).parent))

    def _pick_output(self):
        path = filedialog.askdirectory(title="Choose output folder")
        if path:
            self.output_dir.set(path)

    def _append(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _start(self):
        path = Path(self.source.get())
        output = Path(self.output_dir.get() or path.parent)
        epub_mode = self.mode.get() == "EPUB"
        if not path.is_file() or path.suffix.casefold() != (".docx" if epub_mode else ".pdf"):
            messagebox.showerror("Invalid file", "Choose the correct input file.")
            return
        if not output.is_dir():
            messagebox.showerror("Invalid folder", "Choose an existing output folder.")
            return
        if epub_mode:
            if not self.title_text.get().strip() or not self.author.get().strip():
                messagebox.showerror("Missing metadata", "Enter the EPUB title and author.")
                return
            self._launch(run_epub_job, (path, output, self.title_text.get(), self.author.get(), self.language.get()))
            return
        if not self.watermark.get().strip():
            messagebox.showerror("Invalid watermark", "Enter watermark text.")
            return
        password = simpledialog.askstring("Owner password", "Enter owner password:", show="*", parent=self)
        if password is None:
            return
        confirmation = simpledialog.askstring("Confirm password", "Confirm owner password:", show="*", parent=self)
        if password != confirmation:
            messagebox.showerror("Password mismatch", "The passwords do not match.")
            return
        self._launch(run_pdf_job, (path, output, password, self.watermark.get()))

    def _launch(self, target, args):
        self._append(f"Input: {args[0]}")
        self._append("Starting worker; large files may take a moment to initialize...")
        self.status.set("Starting...")
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.cancel_event.clear()
        self.worker = multiprocessing.Process(
            target=target,
            args=args,
            kwargs={"cancel_event": self.cancel_event, "log_queue": self.log_queue},
            daemon=True,
        )
        self.worker.start()

    def _drain_queue(self):
        try:
            while True:
                kind, value = self.log_queue.get_nowait()
                if kind == "log":
                    self._append(str(value))
                    self.status.set(str(value))
                elif kind == "progress":
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    self.progress_value.set(float(value))
                elif kind == "success":
                    self._finish("Completed successfully.")
                    messagebox.showinfo("Finished", "\n\n".join(value), parent=self)
                elif kind == "cancelled":
                    self._finish("Cancelled.")
                elif kind == "error":
                    self._finish("Failed.")
                    messagebox.showerror("Processing failed", str(value), parent=self)
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def _finish(self, status):
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.status.set(status)
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    SecureNovelApp().mainloop()
