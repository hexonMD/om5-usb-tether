"""Laptop interface for the file-based jewelry shoot runner."""

import math
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, font, ttk

from PIL import Image, ImageOps, ImageTk


class ShootWindow:
    def __init__(self, root, run_job, sample_job, default_output_root, load_job):
        self.root = root
        self.run_job = run_job
        self.sample_job = sample_job
        self.load_job = load_job
        self.frames = []
        self.result_shots = []
        self.directory = None
        self.preview_image = None
        self.preview_tk = None
        self.messages = Queue()
        self.busy = False
        self.closing = False
        self.sku = tk.StringVar(root)
        self.use_still = tk.BooleanVar(root, True)
        self.use_video = tk.BooleanVar(root, False)
        self.video = tk.StringVar(root)
        self.start = tk.StringVar(root, "0")
        self.duration = tk.StringVar(root, "1")
        self.destination = tk.StringVar(root, str(default_output_root()))
        self.status = tk.StringVar(root, "Choose source files or load the sample.")
        self.preview_hint = "Your finished photo will appear here."

        root.title("Jewelry shoot — File mode")
        menu = tk.Menu(root)
        self.file_menu = tk.Menu(menu, tearoff=False)
        self.file_menu.add_command(label="Run shot list…", underline=0, accelerator="Ctrl+O",
                                   command=self.choose_shot_list)
        menu.add_cascade(label="File", underline=0, menu=self.file_menu)
        root.configure(menu=menu)
        root.bind("<Control-o>", lambda _: self.choose_shot_list())
        width, height = root.winfo_screenwidth(), root.winfo_screenheight()
        compact = width < 880 or height < 570
        root.geometry(f"{min(980, width - 28)}x{min(655, height - 75)}")
        root.minsize(min(800, width - 32), min(480, height - 80))
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)
        style = ttk.Style(root)
        families = set(font.families(root))
        family = next((f for f in ("Source Sans 3", "Inter", "Segoe UI", "Helvetica", "DejaVu Sans") if f in families),
                      font.nametofont("TkDefaultFont").actual("family"))
        style.configure("ShootTitle.TLabel", font=(family, 21, "bold"))
        style.configure("ShootHeading.TLabel", font=(family, 12, "bold"))
        style.configure("ShootRun.TButton", font=(family, 11, "bold"), padding=(14, 5))
        header = ttk.Frame(root, padding=(16, 8, 16, 6))
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="Jewelry shoot", style="ShootTitle.TLabel").pack(side="left")
        ttk.Label(header, text="File mode").pack(side="right")

        self.notebook = None
        if compact:
            self.notebook = ttk.Notebook(root)
            self.notebook.grid(row=1, column=0, sticky="nsew", padx=10)
            setup, results = ttk.Frame(self.notebook), ttk.Frame(self.notebook, padding=10)
            self.notebook.add(setup, text="Shoot setup")
            self.notebook.add(results, text="Results")
        else:
            pages = ttk.Frame(root, padding=(12, 0))
            pages.grid(row=1, column=0, sticky="nsew")
            pages.rowconfigure(0, weight=1)
            pages.columnconfigure(0, weight=1, uniform="pages")
            pages.columnconfigure(2, weight=1, uniform="pages")
            setup, results = ttk.Frame(pages), ttk.Frame(pages, padding=10)
            setup.grid(row=0, column=0, sticky="nsew")
            ttk.Separator(pages, orient="vertical").grid(row=0, column=1, sticky="ns", padx=6)
            results.grid(row=0, column=2, sticky="nsew")
        self.results_page = results
        self.make_setup(setup)
        self.make_results(results)

        footer = ttk.Frame(root, padding=(16, 8))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(footer, textvariable=self.status, anchor="w", wraplength=450)
        self.status_label.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.sample_button = ttk.Button(footer, text="Load sample", command=self.load_sample)
        self.sample_button.grid(row=0, column=1, padx=4)
        self.run_button = ttk.Button(footer, text="Run job", style="ShootRun.TButton", command=self.start_job)
        self.run_button.grid(row=0, column=2)
        footer.bind("<Configure>", lambda e: self.status_label.configure(wraplength=max(100, e.width - 265)))
        root.bind("<Control-Return>", lambda _: self.start_job())
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()

    def make_setup(self, parent):
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        canvas = tk.Canvas(parent, highlightthickness=0, width=320, height=200,
                           background=ttk.Style(parent).lookup("TFrame", "background") or "#f0f0f0")
        canvas.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scroll.set)
        form = ttk.Frame(canvas, padding=(6, 4, 8, 6))
        item = canvas.create_window((0, 0), window=form, anchor="nw")
        form.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(item, width=e.width))
        form.columnconfigure(0, weight=1)
        ttk.Label(form, text="Item SKU", style="ShootHeading.TLabel").grid(row=0, sticky="w")
        sku_entry = ttk.Entry(form, textvariable=self.sku)
        sku_entry.grid(row=1, sticky="ew", pady=(2, 9))
        self.still_toggle = ttk.Checkbutton(form, text="Focus-stacked photo", variable=self.use_still, command=self.refresh)
        self.still_toggle.grid(row=2, sticky="w")
        self.frame_list = tk.Listbox(form, height=3, activestyle="dotbox", borderwidth=1,
                                     relief="solid", highlightthickness=0, exportselection=False)
        self.frame_list.grid(row=3, sticky="ew", pady=3)
        self.choose_frames_button = ttk.Button(form, text="Choose focus frames…", command=self.choose_frames)
        self.choose_frames_button.grid(row=4, sticky="w")
        ttk.Separator(form).grid(row=5, sticky="ew", pady=9)
        self.video_toggle = ttk.Checkbutton(form, text="Video clip", variable=self.use_video, command=self.refresh)
        self.video_toggle.grid(row=6, sticky="w")
        video_row = ttk.Frame(form)
        video_row.grid(row=7, sticky="ew", pady=3)
        video_row.columnconfigure(0, weight=1)
        video_entry = ttk.Entry(video_row, textvariable=self.video)
        video_entry.grid(row=0, column=0, sticky="ew")
        browse_video = ttk.Button(video_row, text="Browse…", command=self.choose_video)
        browse_video.grid(row=0, column=1, padx=(4, 0))
        times = ttk.Frame(form)
        times.grid(row=8, sticky="w", pady=2)
        ttk.Label(times, text="Start (s)").grid(row=0, column=0, sticky="w")
        start_entry = ttk.Entry(times, textvariable=self.start, width=8)
        start_entry.grid(row=1, column=0, padx=(0, 16))
        ttk.Label(times, text="Duration (s)").grid(row=0, column=1, sticky="w")
        duration_entry = ttk.Entry(times, textvariable=self.duration, width=8)
        duration_entry.grid(row=1, column=1)
        ttk.Separator(form).grid(row=9, sticky="ew", pady=9)
        ttk.Label(form, text="Save jobs in", style="ShootHeading.TLabel").grid(row=10, sticky="w")
        dest_row = ttk.Frame(form)
        dest_row.grid(row=11, sticky="ew", pady=(2, 0))
        dest_row.columnconfigure(0, weight=1)
        dest = ttk.Entry(dest_row, textvariable=self.destination)
        dest.grid(row=0, column=0, sticky="ew")
        browse_dest = ttk.Button(dest_row, text="Browse…", command=self.choose_destination)
        browse_dest.grid(row=0, column=1, padx=(4, 0))
        self.common_inputs = [sku_entry, self.still_toggle, self.video_toggle, dest, browse_dest]
        self.video_inputs = [video_entry, browse_video, start_entry, duration_entry]

        def scroll_to_focus(event):
            if event.widget is canvas or not event.widget.winfo_ismapped():
                return
            top = event.widget.winfo_rooty() - form.winfo_rooty()
            visible = canvas.canvasy(0)
            viewport = canvas.winfo_height()
            bottom = top + event.widget.winfo_height()
            if top < visible or bottom > visible + viewport:
                target = top if top < visible else bottom - viewport
                canvas.yview_moveto(max(0, target) / max(1, form.winfo_height()))

        for widget in [*self.common_inputs, *self.video_inputs, self.frame_list, self.choose_frames_button]:
            widget.bind("<FocusIn>", scroll_to_focus)
        sku_entry.focus_set()

    def make_results(self, pane):
        pane.columnconfigure(0, weight=1)
        pane.rowconfigure(1, weight=1)
        ttk.Label(pane, text="Output preview", style="ShootHeading.TLabel").grid(row=0, sticky="w")
        self.canvas = tk.Canvas(pane, background="#e6e5df", highlightthickness=0, width=300, height=140)
        self.canvas.grid(row=1, column=0, sticky="nsew", pady=6)
        self.canvas.bind("<Configure>", lambda _: self.redraw())
        files = ttk.Frame(pane)
        files.grid(row=2, column=0, sticky="ew")
        files.columnconfigure(0, weight=1)
        self.result_files = tk.Listbox(files, height=3, exportselection=False, activestyle="dotbox")
        self.result_files.grid(row=0, column=0, sticky="ew")
        scroll = ttk.Scrollbar(files, orient="vertical", command=self.result_files.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.result_files.configure(yscrollcommand=scroll.set)
        self.result_files.bind("<<ListboxSelect>>", lambda _: self.show_selected_output())
        actions = ttk.Frame(pane)
        actions.grid(row=3, sticky="w", pady=(5, 0))
        self.open_button = ttk.Button(actions, text="Open results folder", command=self.open_folder)
        self.open_button.grid(row=0, column=0, sticky="w")
        self.export_button = ttk.Button(actions, text="Send stills to editor folder…", command=self.send_stills)
        self.export_button.grid(row=0, column=1, padx=(4, 0), sticky="w")

    def choose_frames(self):
        paths = filedialog.askopenfilenames(parent=self.root, title="Choose aligned focus frames",
                                            filetypes=[("JPEG images", "*.jpg *.jpeg *.JPG *.JPEG")])
        if paths:
            self.frames = list(paths)
            self.refresh()

    def choose_video(self):
        path = filedialog.askopenfilename(parent=self.root, title="Choose a video",
                                          filetypes=[("Video files", "*.mov *.mp4 *.m4v *.mkv *.avi"), ("All files", "*")])
        if path:
            self.video.set(path)

    def choose_destination(self):
        path = filedialog.askdirectory(parent=self.root, title="Choose the output folder")
        if path:
            self.destination.set(path)

    def choose_shot_list(self):
        if not self.busy:
            path = filedialog.askopenfilename(parent=self.root, title="Run a shot list or saved manifest",
                                              filetypes=[("JSON shot lists", "*.json"), ("All files", "*")])
            if path:
                self.start_job(shot_list=path)
        return "break"

    def load_sample(self):
        try:
            job = self.sample_job()
            still = next(s for s in job["shots"] if s["type"] == "focus_still")
            video = next(s for s in job["shots"] if s["type"] == "video")
            self.sku.set(job["sku"])
            self.frames = list(still["frames"])
            self.video.set(video["source"])
            self.start.set(str(video["start_seconds"]))
            self.duration.set(str(video["duration_seconds"]))
            self.use_still.set(True)
            self.use_video.set(True)
            self.status.set("Sample ready. Sources are simulated camera files.")
            self.refresh()
            if self.notebook:
                self.notebook.select(0)
        except (OSError, ValueError, KeyError, StopIteration) as error:
            self.status.set(f"Could not load the sample. {error}")

    def refresh(self):
        self.file_menu.entryconfigure(0, state="disabled" if self.busy else "normal")
        for widget in self.common_inputs + [self.sample_button, self.run_button]:
            widget.state(["disabled" if self.busy else "!disabled"])
        self.choose_frames_button.state(["!disabled" if self.use_still.get() and not self.busy else "disabled"])
        self.frame_list.configure(state="normal")
        self.frame_list.delete(0, "end")
        for path in self.frames:
            self.frame_list.insert("end", Path(path).name)
        self.frame_list.configure(state="normal" if self.use_still.get() and not self.busy else "disabled")
        for widget in self.video_inputs:
            widget.state(["!disabled" if self.use_video.get() and not self.busy else "disabled"])
        self.open_button.state(["!disabled" if self.directory else "disabled"])
        has_stills = any(shot["type"] == "focus_still" for shot in self.result_shots)
        self.export_button.state(["!disabled" if has_stills and not self.busy else "disabled"])

    def build_job(self):
        sku = self.sku.get().strip()
        if not sku:
            raise ValueError("Enter the item SKU.")
        shots = []
        if self.use_still.get():
            if len(self.frames) < 2:
                raise ValueError("Choose at least two aligned JPEG frames at different focus positions.")
            shots.append({"type": "focus_still", "name": "detail", "frames": self.frames.copy()})
        if self.use_video.get():
            if not self.video.get().strip():
                raise ValueError("Choose a source video.")
            try:
                start, duration = float(self.start.get()), float(self.duration.get())
            except ValueError:
                raise ValueError("Enter the start and duration in seconds.") from None
            if not (math.isfinite(start) and start >= 0 and math.isfinite(duration) and duration > 0):
                raise ValueError("Use a nonnegative start and a positive duration in seconds.")
            shots.append({"type": "video", "name": "product-clip", "source": self.video.get().strip(), "start_seconds": start,
                          "duration_seconds": duration})
        if not shots:
            raise ValueError("Select a photo, a video, or both.")
        return {"sku": sku, "shots": shots}

    def start_job(self, *, shot_list=None):
        if self.busy:
            return
        try:
            job = None if shot_list else self.build_job()
            if not self.destination.get().strip():
                raise ValueError("Choose the output folder.")
        except ValueError as error:
            self.status.set(str(error))
            return
        destination = Path(self.destination.get().strip()).expanduser()
        if not self.directory:
            self.preview_hint = "Processing…"
        self.status.set("Starting job…")
        self.redraw()

        def run():
            plan = self.load_job(shot_list) if shot_list else job
            return self.run_job(plan, destination, progress=lambda s: self.messages.put(("status", str(s))))

        self.start_worker(run, "job")

    def send_stills(self):
        if self.busy or not self.directory:
            return
        folder = filedialog.askdirectory(parent=self.root, title="Choose the editor folder", mustexist=True)
        if not folder:
            return
        manifest = self.directory / "manifest.json"
        self.status.set("Sending stills…")

        def export():
            if __package__:
                from .jewelry_export import export_stills
            else:
                from jewelry_export import export_stills
            return export_stills(manifest, folder)

        self.start_worker(export, "export")

    def start_worker(self, work, operation):
        self.busy = True
        self.refresh()

        def process():
            try:
                self.messages.put((operation, work()))
            except Exception as error:
                self.messages.put((operation + "_error", str(error)))

        threading.Thread(target=process, name="jewelry-" + operation).start()
        self.root.after(80, self.poll)

    def poll(self):
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "status":
                    self.status.set(value)
                    continue
                self.busy = False
                if self.closing:
                    self.root.destroy()
                    return
                if kind == "job_error":
                    if not self.directory:
                        self.preview_hint = "Job incomplete."
                    self.status.set(f"Job failed. {value}")
                elif kind == "export_error":
                    self.status.set(f"Could not send the stills. {value}")
                elif kind == "export":
                    copied, existing = value["copied"], value["existing"]
                    self.status.set(f"Sent {copied} new, {existing} already present in {value['inbox']}.")
                else:
                    self.directory = Path(value["directory"])
                    self.status.set(f"Completed {value['sku']}.")
                    self.result_shots = value["shots"]
                    self.result_files.delete(0, "end")
                    for shot in self.result_shots:
                        self.result_files.insert("end", f"{shot['file']}  ·  {shot['width']} × {shot['height']}")
                    initial = next((i for i, shot in enumerate(self.result_shots) if shot["type"] == "focus_still"), 0)
                    self.result_files.selection_set(initial)
                    self.result_files.see(initial)
                    self.show_selected_output()
                    if self.notebook:
                        self.notebook.select(self.results_page)
                self.redraw()
                self.refresh()
                return
        except Empty:
            self.root.after(80, self.poll)

    def show_selected_output(self):
        selected = self.result_files.curselection()
        if not selected or not self.directory:
            return
        shot = self.result_shots[selected[0]]
        self.preview_image = None
        self.preview_hint = "Open the results folder to play this video."
        if shot["type"] == "focus_still":
            try:
                with Image.open(self.directory / shot["file"]) as image:
                    image.draft("RGB", (1280, 1000))
                    self.preview_image = ImageOps.exif_transpose(image).convert("RGB")
                    self.preview_image.thumbnail((1280, 1000))
            except (OSError, ValueError) as error:
                self.preview_hint = f"Could not open the photo preview. {error}"
        self.redraw()

    def redraw(self):
        self.canvas.delete("all")
        w, h = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        if w < 2 or h < 2:
            return
        if self.preview_image is None:
            self.preview_tk = None
            self.canvas.create_text(w/2, h/2, text=self.preview_hint, fill="#48483e", width=max(w-16, 1))
        else:
            iw, ih = self.preview_image.size
            scale = min(w / iw, h / ih)
            size = (max(1, round(iw * scale)), max(1, round(ih * scale)))
            image = self.preview_image.resize(size, Image.Resampling.LANCZOS)
            self.preview_tk = ImageTk.PhotoImage(image, master=self.root)
            self.canvas.create_image(w/2, h/2, image=self.preview_tk)

    def open_folder(self):
        if self.directory:
            try:
                if os.name == "nt":
                    os.startfile(self.directory)
                else:
                    subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(self.directory)])
            except OSError as error:
                self.status.set(f"Could not open the folder. {error}")

    def close(self):
        if self.busy:
            self.closing = True
            self.root.withdraw()
        else:
            self.root.destroy()


def launch():
    if __package__:
        from .jewelry_job import default_output_root, load_job, run_job, sample_job
    else:
        from jewelry_job import default_output_root, load_job, run_job, sample_job

    root = tk.Tk()
    ShootWindow(root, run_job, sample_job, default_output_root, load_job)
    root.mainloop()


if __name__ == "__main__":
    launch()
