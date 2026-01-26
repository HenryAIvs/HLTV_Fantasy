import tkinter as tk
from tkinter import ttk, messagebox
import json

from player_db import add_or_update_player, get_player


# Your 18 boosters: indices 0–17 → HLTV booster IDs
BOOSTER_SOURCE_IDS = [
    2,   # your 0
    3,   # your 1
    5,   # your 2
    8,   # your 3
    9,   # your 4
    13,  # your 5
    16,  # your 6
    18,  # your 7
    19,  # your 8
    20,  # your 9
    21,  # your 10
    22,  # your 11
    23,  # your 12
    26,  # your 13
    27,  # your 14
    28,  # your 15
    29,  # your 16
    30,  # your 17
]

# Your 12 roles: indices 0–11 → HLTV role IDs (skip JSON 3 and 4)
ROLE_SOURCE_IDS = [
    0,   # your role 0
    1,   # your role 1
    2,   # your role 2
    5,   # your role 3
    6,   # your role 4
    7,   # your role 5
    8,   # your role 6
    9,   # your role 7
    10,  # your role 8
    11,  # your role 9
    12,  # your role 10
    13,  # your role 11
]


class TriggerRatesImportTab:
    def __init__(self, parent):
        self.frame = ttk.Frame(parent)
        self.build()

    def build(self):
        f = self.frame

        ttk.Label(
            f,
            text="Import Trigger Rates JSON (paste contents below)",
            font=("Arial", 12, "bold")
        ).pack(pady=10)

        ttk.Label(
            f,
            text="Paste the full triggerRates JSON here, then click 'Import JSON'."
        ).pack(pady=5)

        self.text = tk.Text(f, width=100, height=25)
        self.text.pack(pady=10)
        sb = ttk.Scrollbar(f, command=self.text.yview)
        sb.pack(side="right", fill="y")
        self.text.configure(yscrollcommand=sb.set)

        ttk.Button(
            f,
            text="Import JSON",
            command=self.import_from_text
        ).pack(pady=10)

        self.output = tk.Text(f, width=100, height=10)
        self.output.pack(pady=10)
        out_sb = ttk.Scrollbar(f, command=self.output.yview)
        out_sb.pack(side="right", fill="y")
        self.output.configure(yscrollcommand=out_sb.set)

    # ------------------------------------------------------------

    def import_from_text(self):
        raw = self.text.get("1.0", tk.END).strip()
        if not raw:
            messagebox.showerror("Error", "JSON input is empty.")
            return

        try:
            data = json.loads(raw)
        except Exception as e:
            messagebox.showerror("Error", f"Invalid JSON: {e}")
            return

        try:
            ptr_list = data.get("playerTriggerRates") or []
            if not ptr_list:
                messagebox.showerror("Error", "No 'playerTriggerRates' field in JSON.")
                return

            self.output.delete(1.0, tk.END)
            self.output.insert(tk.END, f"Loaded {len(ptr_list)} entries from JSON.\n\n")

            updated = 0

            for entry in ptr_list:
                pid = entry.get("playerId", {}).get("playerId")
                if pid is None:
                    continue

                booster_map_raw = entry.get("boosterIdToTriggerRate", {})
                role_map_raw = entry.get("roleIdToTriggerRate", {})

                # ---------- BOOSTERS: your 0–17 mapped from HLTV IDs ----------
                boosters_json: dict[str, float] = {}
                for idx, src_id in enumerate(BOOSTER_SOURCE_IDS):
                    obj = booster_map_raw.get(str(src_id), {})
                    val = float(obj.get("value", 0.0))
                    boosters_json[str(idx)] = val

                # ---------- ROLES: your 0–11 mapped from HLTV role IDs ----------
                roles_json: dict[str, dict] = {}
                for idx, src_id in enumerate(ROLE_SOURCE_IDS):
                    rdata = role_map_raw.get(str(src_id), {})
                    small = rdata.get("smallPoints", {})
                    maxp = rdata.get("maxPoints", {})
                    roles_json[str(idx)] = {
                        "major": float(maxp.get("value", 0.0)),
                        "minor": float(small.get("value", 0.0)),
                    }

                # Keep core info from existing player if present
                existing = get_player(pid)
                if existing:
                    name = existing["name"]
                    rating = existing.get("rating", 0.0)
                    price = existing.get("price", 0)
                    best_role = existing.get("best_role", "")
                    major_win_pct = existing.get("major_win_pct", 0.0)
                    minor_win_pct = existing.get("minor_win_pct", 0.0)
                else:
                    name = f"Player {pid}"
                    rating = 0.0
                    price = 0
                    best_role = ""
                    major_win_pct = 0.0
                    minor_win_pct = 0.0

                add_or_update_player(
                    player_id=pid,
                    name=name,
                    rating=rating,
                    price=price,
                    best_role=best_role,
                    major_win_pct=major_win_pct,
                    minor_win_pct=minor_win_pct,
                    boosters_json=boosters_json,
                    roles_json=roles_json,
                )

                updated += 1

                # Log: best booster + best role among your mapped set
                best_booster_id = max(boosters_json, key=lambda k: boosters_json[k]) if boosters_json else None
                best_booster_pct = int(round(boosters_json[best_booster_id] * 100)) if best_booster_id else 0

                best_role_id = None
                best_role_major = -1.0
                best_role_minor = 0.0
                for rid, vals in roles_json.items():
                    major = vals.get("major", 0.0)
                    if major > best_role_major:
                        best_role_major = major
                        best_role_minor = vals.get("minor", 0.0)
                        best_role_id = rid
                role_major_pct = int(round(best_role_major * 100)) if best_role_major >= 0 else 0
                role_minor_pct = int(round(best_role_minor * 100)) if best_role_major >= 0 else 0

                self.output.insert(
                    tk.END,
                    f"Player {pid} — best booster {best_booster_id} ({best_booster_pct}%), "
                    f"best role {best_role_id} (major {role_major_pct}%, minor {role_minor_pct}%)\n"
                )

            self.output.insert(tk.END, f"\nDone. Updated {updated} players.\n")
            messagebox.showinfo("Import complete", f"Updated {updated} players from pasted JSON.")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to process triggerRates: {e}")
