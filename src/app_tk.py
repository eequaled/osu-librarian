"""Tkinter desktop UI: same filters as the HTML report + mode switch + multiselect.

Run: python3 main.py ui --in library.json
"""
from __future__ import annotations

import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .config import Settings, load_settings, save_settings
from .library import Filters, apply_filters, from_dict_list, group_mapsets


class App(tk.Tk):
    def __init__(self, maps):
        super().__init__()
        self.title("osu! Librarian (MVP)")
        self.geometry("1080x640")
        self.all_maps = maps
        self.view = tk.StringVar(value="difficulty")
        self.played = tk.StringVar(value="all")
        self.mode = tk.StringVar(value="all")
        self.sort = tk.StringVar(value="title")
        self.ranked = tk.StringVar(value="all")
        self.query = tk.StringVar()
        self.smin = tk.DoubleVar(value=0.0)
        self.smax = tk.DoubleVar(value=99.0)
        self.settings = load_settings()
        self._build()
        self.refresh()

    def _build(self):
        bar = ttk.Frame(self, padding=8)
        bar.pack(fill="x")
        ttk.Label(bar, text="search").pack(side="left")
        q = ttk.Entry(bar, textvariable=self.query, width=30)
        q.pack(side="left", padx=4)
        q.bind("<KeyRelease>", lambda _e: self.refresh())
        for label, var, vals in [
            ("played", self.played, ["all", "played", "unplayed"]),
            ("mode", self.mode, ["all", "osu", "taiko", "catch", "mania"]),
            ("ranked", self.ranked, ["all", "ranked", "loved", "qualified", "pending", "unknown"]),
            ("sort", self.sort, ["title", "artist", "creator", "bpm", "length", "stars", "rank"]),
            ("view", self.view, ["difficulty", "mapset"]),
        ]:
            ttk.Label(bar, text=label).pack(side="left", padx=(8, 0))
            cb = ttk.Combobox(bar, textvariable=var, values=vals, width=10, state="readonly")
            cb.pack(side="left")
            cb.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
        f2 = ttk.Frame(self, padding=(8, 0))
        f2.pack(fill="x")
        ttk.Label(f2, text="stars").pack(side="left")
        ttk.Spinbox(f2, textvariable=self.smin, from_=0, to=99, increment=0.5, width=5,
                    command=self.refresh).pack(side="left", padx=4)
        ttk.Label(f2, text="–").pack(side="left")
        ttk.Spinbox(f2, textvariable=self.smax, from_=0, to=99, increment=0.5, width=5,
                    command=self.refresh).pack(side="left", padx=4)
        ttk.Label(f2, text=f"mode: {self.settings.mode}").pack(side="left", padx=12)
        ttk.Button(f2, text="stable", command=lambda: self.set_mode("stable")).pack(side="left")
        ttk.Button(f2, text="lazer", command=lambda: self.set_mode("lazer")).pack(side="left", padx=4)
        ttk.Button(f2, text="select filtered", command=self.select_all).pack(side="right", padx=2)
        ttk.Button(f2, text="clear", command=self.select_none).pack(side="right", padx=2)
        ttk.Button(f2, text="invert", command=self.invert).pack(side="right", padx=2)
        ttk.Button(f2, text="export json", command=self.export).pack(side="right", padx=2)

        self.stats = ttk.Label(self, padding=(8, 0))
        self.stats.pack(fill="x")
        cols = ("played", "title", "mode", "stars", "grade")
        self.tree = ttk.Treeview(self, columns=cols, show="tree headings", selectmode="extended")
        self.tree.heading("#0", text="✓")
        self.tree.column("#0", width=40)
        for c, w in [("played", 60), ("title", 560), ("mode", 70), ("stars", 60), ("grade", 60)]:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w)
        self.tree.pack(fill="both", expand=True, padx=8, pady=8)
        self.tree.bind("<Double-1>", self.toggle)
        self.tree.bind("<space>", self.toggle)
        self.checked: set[str] = set()
        self.row_ids: list[str] = []

    def set_mode(self, m: str):
        self.settings.mode = m
        save_settings(self.settings)
        messagebox.showinfo("mode", f"Mode set to {m}. Re-run scan with --mode {m} to rebuild library.json.")

    def current(self):
        try:
            f = Filters(text=self.query.get(), played=self.played.get(), mode=self.mode.get(),
                        star_min=float(self.smin.get()), star_max=float(self.smax.get()),
                        ranked=self.ranked.get(), sort=self.sort.get())
        except tk.TclError:
            f = Filters()
        return apply_filters(self.all_maps, f)

    def refresh(self):
        rows = self.current()
        for i in self.tree.get_children():
            self.tree.delete(i)
        self.row_ids = [b.id for b in rows]
        played = sum(1 for b in rows if b.played)
        self.stats.config(text=f"{len(rows)}/{len(self.all_maps)} shown · played: {played} · "
                               f"unplayed: {len(rows) - played} · selected: {len(self.checked)}")
        if self.view.get() == "mapset":
            for ms in group_mapsets(rows):
                node = self.tree.insert("", "end", text="▸", values=(
                    f"{ms.played_count}/{len(ms.beatmaps)}", f"{ms.artist} — {ms.title}", "", f"{ms.max_stars:.1f}", ""))
                for b in ms.beatmaps:
                    mark = "☑" if b.id in self.checked else "☐"
                    self.tree.insert(node, "end", text=mark, values=(
                        "yes" if b.played else "no", f"[{b.diff}] {b.creator}",
                        b.mode_name, f"{b.stars:.2f}", b.grade or "—"), tags=(b.id,))
        else:
            for b in rows:
                mark = "☑" if b.id in self.checked else "☐"
                self.tree.insert("", "end", text=mark, values=(
                    "yes" if b.played else "no", f"{b.artist} — {b.title} [{b.diff}]",
                    b.mode_name, f"{b.stars:.2f}", b.grade or "—"), tags=(b.id,))

    def _ids_in_view(self) -> list[str]:
        return list(self.row_ids)

    def toggle(self, _e=None):
        sel = self.tree.selection()
        for item in sel or self.tree.get_children():
            tags = self.tree.item(item, "tags")
            if tags:
                bid = tags[0]
                if bid in self.checked:
                    self.checked.discard(bid)
                else:
                    self.checked.add(bid)
        self.refresh()
        return "break"

    def select_all(self):
        self.checked.update(self._ids_in_view())
        self.refresh()

    def select_none(self):
        self.checked.clear()
        self.refresh()

    def invert(self):
        inv = {i for i in self._ids_in_view() if i not in self.checked}
        self.checked = inv | (self.checked - set(self._ids_in_view()))
        self.refresh()

    def export(self):
        sel = [b.__dict__ for b in self.all_maps if b.id in self.checked]
        path = filedialog.asksaveasfilename(defaultextension=".json",
                                            filetypes=[("JSON", "*.json")])
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(sel, f, indent=1)
            messagebox.showinfo("export", f"exported {len(sel)} beatmaps")


def run(maps):
    App(maps).mainloop()


def load_library(path: str):
    with open(path, encoding="utf-8") as f:
        return from_dict_list(json.load(f))
