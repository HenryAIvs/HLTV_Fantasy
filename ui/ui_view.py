import json
import tkinter as tk
from tkinter import ttk

from db_helpers import fetch_table
from player_db import get_player_with_parsed_json


# Fill these with your real names when you’re ready.
# Keys are string IDs matching your boosters_json / roles_json keys.
BOOSTER_NAMES = {
    "0": "Best Pistol Round",
    "1": "Bottom of scoreboard",
    "2": "Clutch",
    "3": "Top of scoreboard",
    "4": "Avenger",
    "5": "Bait",
    "6": "Rambo",
    "7": "Flash",
    "8": "Mister consistent",
    "9": "Kobe",
    "10": "Saver",
    "11": "Assist",
    "12": "Aim bot",
    "13": "Quad",
    "14": "Carry",
    "15": "Cannon fodder",
    "16": "Farmer",
    "17": "Hellcase",
}


ROLE_NAMES = {
    "0": "Main AWP",
    "1": "Support",
    "2": "Attacker",
    "3": "Leader",
    "4": "Stathunter",
    "5": "Entry Fragger",
    "6": "Camper",
    "7": "Defender",
    "8": "HS Machine",
    "9": "Noob",
    "10": "Multi Fragger",
    "11": "Eco Friendly",
}


class ViewTab:
    def __init__(self, parent):
        self.frame = ttk.Frame(parent)
        self.build()

    def build(self):
        f = self.frame

        # Table selector
        self.table_var = tk.StringVar(value="players")
        ttk.OptionMenu(f, self.table_var, "players", "players", "teams").pack(pady=10)

        ttk.Button(f, text="Refresh", command=self.refresh).pack(pady=5)

        # Main table
        self.tree = ttk.Treeview(f)
        self.tree.pack(fill="both", expand=True)

        # Detail panel for roles/boosters
        detail_frame = ttk.Frame(f)
        detail_frame.pack(fill="both", expand=False, pady=5)

        ttk.Label(detail_frame, text="Player Roles & Boosters Detail").pack(anchor="w")

        self.detail_text = tk.Text(detail_frame, width=100, height=12)
        self.detail_text.pack(fill="both", expand=True)

        sb = ttk.Scrollbar(detail_frame, command=self.detail_text.yview)
        sb.pack(side="right", fill="y")
        self.detail_text.configure(yscrollcommand=sb.set)

        # Bind selection
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

    # ------------------------------------------------------------
    # Helpers to parse and pretty-print JSON
    # ------------------------------------------------------------

    @staticmethod
    def _parse_json(text):
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            return None

    def _format_boosters(self, boosters: dict | None) -> str:
        if not boosters:
            return "No boosters_json data.\n"

        lines = ["Boosters:"]
        for bid_str, val in sorted(boosters.items(), key=lambda x: int(x[0])):
            try:
                fv = float(val)
            except Exception:
                fv = 0.0
            pct = int(round(fv * 100))
            name = BOOSTER_NAMES.get(bid_str, f"Booster {bid_str}")
            lines.append(f"  [{bid_str}] {name}: {pct}% (raw {fv:.3f})")
        return "\n".join(lines) + "\n"

    def _format_roles(self, roles: dict | None) -> str:
        if not roles:
            return "No roles_json data.\n"

        lines = ["Roles:"]
        for rid_str, vals in sorted(roles.items(), key=lambda x: int(x[0])):
            if not isinstance(vals, dict):
                continue
            major = float(vals.get("major", 0.0))
            minor = float(vals.get("minor", 0.0))
            major_pct = int(round(major * 100))
            minor_pct = int(round(minor * 100))
            name = ROLE_NAMES.get(rid_str, f"Role {rid_str}")
            lines.append(
                f"  [{rid_str}] {name}: major {major_pct}% (raw {major:.3f}), "
                f"minor {minor_pct}% (raw {minor:.3f})"
            )
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------
    # Refresh logic
    # ------------------------------------------------------------

    def refresh(self):
        table = self.table_var.get()
        rows = fetch_table(table)

        self.tree.delete(*self.tree.get_children())
        self.tree["columns"] = ()

        # Also clear detail pane when changing or refreshing
        self.detail_text.delete("1.0", tk.END)

        if not rows:
            return

        if table == "players":
            self._refresh_players(rows)
        else:
            self._refresh_generic(rows)

    def _refresh_players(self, rows):
        """
        For players, show id, name, rating, price, and simple best booster/role summary.
        """
        cols = [
            "player_id",
            "name",
            "rating",
            "price",
            "best_booster_id",
            "best_booster_pct",
            "best_role_id",
            "best_role_major_pct",
            "best_role_minor_pct",
        ]
        self.tree["columns"] = cols
        self.tree.column("#0", width=0)
        self.tree.heading("#0", text="")

        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=130, anchor="center")

        for r in rows:
            d = dict(r)
            pid = d.get("player_id")
            name = d.get("name")
            rating = d.get("rating", 0.0)
            price = d.get("price", 0)

            # Compute best booster & role from JSON if present
            boosters = self._parse_json(d.get("boosters_json"))
            roles = self._parse_json(d.get("roles_json"))

            # best booster by highest value
            best_booster_id = None
            best_booster_val = -1.0
            if isinstance(boosters, dict):
                for bid_str, val in boosters.items():
                    try:
                        fv = float(val)
                    except Exception:
                        fv = 0.0
                    if fv > best_booster_val:
                        best_booster_val = fv
                        best_booster_id = bid_str
            booster_pct = int(round(best_booster_val * 100)) if best_booster_val > 0 else 0

            # best role by max points
            best_role_id = None
            best_major_pct = 0
            best_minor_pct = 0
            if isinstance(roles, dict):
                best_score = -1e9
                for rid_str, vals in roles.items():
                    if not isinstance(vals, dict):
                        continue
                    major = float(vals.get("major", 0.0))
                    minor = float(vals.get("minor", 0.0))
                    score = 5.0 * major + 2.0 * minor - 2.0 * (1.0 - major - minor)
                    if score > best_score:
                        best_score = score
                        best_role_id = rid_str
                        best_major_pct = int(round(major * 100))
                        best_minor_pct = int(round(minor * 100))

            self.tree.insert(
                "",
                "end",
                values=[
                    pid,
                    name,
                    f"{float(rating):.2f}",
                    price,
                    best_booster_id if best_booster_id is not None else "-",
                    f"{booster_pct}%",
                    best_role_id if best_role_id is not None else "-",
                    f"{best_major_pct}%",
                    f"{best_minor_pct}%",
                ],
            )

    def _refresh_generic(self, rows):
        """
        For teams (or any other table), show all columns as-is.
        """
        cols = list(rows[0].keys())
        self.tree["columns"] = cols
        self.tree.column("#0", width=0)
        self.tree.heading("#0", text="")

        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=140, anchor="center")

        for r in rows:
            d = dict(r)
            self.tree.insert("", "end", values=[d[c] for c in cols])

    # ------------------------------------------------------------
    # Selection handler: show full roles/boosters for selected player
    # ------------------------------------------------------------

    def _on_select(self, event):
        table = self.table_var.get()
        if table != "players":
            # Only show detail when viewing players
            return

        sel = self.tree.selection()
        if not sel:
            return

        item_id = sel[0]
        vals = self.tree.item(item_id, "values")
        if not vals:
            return

        try:
            player_id = int(vals[0])
        except (ValueError, TypeError):
            return

        # Fetch player with parsed JSON from DB
        pdata = get_player_with_parsed_json(player_id)
        if not pdata:
            self.detail_text.delete("1.0", tk.END)
            self.detail_text.insert(tk.END, f"No player data for id {player_id}\n")
            return

        boosters = pdata.get("boosters")
        roles = pdata.get("roles")

        text = []
        text.append(f"Player {player_id}: {pdata.get('name', '')}")
        text.append(f"Rating: {pdata.get('rating', 0.0):.2f}, Price: {pdata.get('price', 0)}")
        text.append("")  # blank line
        text.append(self._format_boosters(boosters).rstrip())
        text.append(self._format_roles(roles).rstrip())

        self.detail_text.delete("1.0", tk.END)
        self.detail_text.insert(tk.END, "\n".join(text) + "\n")
