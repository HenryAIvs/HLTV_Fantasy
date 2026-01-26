# ui/ui_import_ratings.py

import math
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import pandas as pd

from player_db import add_or_update_player, get_player_by_name


class RatingsImportTab:
    def __init__(self, parent):
        self.frame = ttk.Frame(parent)
        self.build()

    def build(self):
        f = self.frame

        ttk.Label(
            f,
            text="Import Player Rating Tiers (vs Top X Opponents)",
            font=("Arial", 12, "bold")
        ).pack(pady=10)

        ttk.Label(
            f,
            text=(
                "Select the cleaned ratings spreadsheet "
                "(e.g. Player_top_x_cleaned_roles_boosters_filtered.xlsx).\n"
                "It must have columns: player_name, rating_top5, maps_top5, "
                "rating_top10, maps_top10, rating_top20, maps_top20, "
                "rating_top30, maps_top30, rating_top50, maps_top50.\n"
                "Any tier with < 5 maps should already be blank."
            ),
            justify="left",
            wraplength=900
        ).pack(pady=5)

        ttk.Button(
            f,
            text="Load ratings file and import",
            command=self.load_and_import
        ).pack(pady=10)

        self.output = tk.Text(f, width=110, height=30)
        self.output.pack(pady=10)

        sb = ttk.Scrollbar(f, command=self.output.yview)
        sb.pack(side="right", fill="y")
        self.output.configure(yscrollcommand=sb.set)

    # ------------------------------------------------------------

    def load_and_import(self):
        path = filedialog.askopenfilename(
            title="Select cleaned ratings Excel file",
            filetypes=[("Excel files", "*.xlsx *.xls"), ("All files", "*.*")]
        )
        if not path:
            return

        try:
            df = pd.read_excel(path)
        except Exception as e:
            messagebox.showError("Error", f"Failed to read Excel file:\n{e}")
            return

        required_cols = [
            "player_name",
            "rating_top5", "maps_top5",
            "rating_top10", "maps_top10",
            "rating_top20", "maps_top20",
            "rating_top30", "maps_top30",
            "rating_top50", "maps_top50",
        ]
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            messagebox.showerror("Error", f"Missing required columns: {missing}")
            return

        self.output.delete(1.0, tk.END)
        self.output.insert(tk.END, f"Loaded {len(df)} rows from {path}\n\n")

        updated = 0
        skipped = 0

        for _, row in df.iterrows():
            name = str(row["player_name"]).strip()
            if not name:
                continue

            player = get_player_by_name(name)
            if not player:
                self.output.insert(
                    tk.END,
                    f"[SKIP] No player found in DB with name '{name}'\n"
                )
                skipped += 1
                continue

            pid = player["player_id"]

            def safe_num(v):
                if isinstance(v, (int, float)) and not math.isnan(v):
                    return float(v)
                return None

            rating_top5 = safe_num(row.get("rating_top5"))
            maps_top5 = safe_num(row.get("maps_top5"))
            rating_top10 = safe_num(row.get("rating_top10"))
            maps_top10 = safe_num(row.get("maps_top10"))
            rating_top20 = safe_num(row.get("rating_top20"))
            maps_top20 = safe_num(row.get("maps_top20"))
            rating_top30 = safe_num(row.get("rating_top30"))
            maps_top30 = safe_num(row.get("maps_top30"))
            rating_top50 = safe_num(row.get("rating_top50"))
            maps_top50 = safe_num(row.get("maps_top50"))

            # IMPORTANT: pass 'name' so NOT NULL constraint is satisfied
            add_or_update_player(
                player_id=pid,
                name=player["name"],  # keep existing DB name
                rating_top5=rating_top5,
                maps_top5=maps_top5,
                rating_top10=rating_top10,
                maps_top10=maps_top10,
                rating_top20=rating_top20,
                maps_top20=maps_top20,
                rating_top30=rating_top30,
                maps_top30=maps_top30,
                rating_top50=rating_top50,
                maps_top50=maps_top50,
            )

            updated += 1
            self.output.insert(
                tk.END,
                f"[OK] Updated {name} (id={pid}) with rating tiers.\n"
            )

        self.output.insert(
            tk.END,
            f"\nDone. Updated {updated} players. Skipped {skipped} (no DB match).\n"
        )
        messagebox.showinfo(
            "Import complete",
            f"Updated {updated} players.\nSkipped {skipped} (no match in DB)."
        )
