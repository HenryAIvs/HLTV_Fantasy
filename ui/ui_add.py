import tkinter as tk
from tkinter import ttk, messagebox

from team_db import add_or_update_team
from player_db import add_or_update_player


class AddTab:
    def __init__(self, parent):
        self.frame = ttk.Frame(parent)
        self.build()

    def build(self):
        nb = ttk.Notebook(self.frame)
        nb.pack(fill="both", expand=True)

        self.player_tab = ttk.Frame(nb)
        self.team_tab = ttk.Frame(nb)

        nb.add(self.player_tab, text="Add Player")
        nb.add(self.team_tab, text="Add Team")

        self.build_players()
        self.build_teams()

    # ============================================================
    # ADD PLAYER
    # ============================================================

    def build_players(self):
        f = self.player_tab

        # We no longer ask for booster values here;
        # boosters & roles come from triggerRates JSON importer.
        labels = [
            "Player ID",
            "Name",
            "Rating",
            "Price",
            "Best Role",
            "Major Win %",
            "Minor Win %",
        ]

        self.player_entries = []

        for lab in labels:
            row = ttk.Frame(f)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=lab, width=20).pack(side="left")
            e = ttk.Entry(row)
            e.pack(side="left", expand=True, fill="x")
            self.player_entries.append(e)

        ttk.Button(
            f,
            text="Add Player",
            command=self.submit_player
        ).pack(pady=10)

    def submit_player(self):
        try:
            vals = [e.get() for e in self.player_entries]

            player_id = int(vals[0])
            name = vals[1]
            rating = float(vals[2])
            price = int(vals[3])
            best_role = vals[4]
            major_win_pct = float(vals[5]) if vals[5] else 0.0
            minor_win_pct = float(vals[6]) if vals[6] else 0.0

            # boosters_json and roles_json are None here;
            # they will be filled in later by the triggerRates importer.
            add_or_update_player(
                player_id=player_id,
                name=name,
                rating=rating,
                price=price,
                best_role=best_role,
                major_win_pct=major_win_pct,
                minor_win_pct=minor_win_pct,
                boosters_json=None,
                roles_json=None,
            )

            messagebox.showinfo("Success", "Player added/updated.")

        except Exception as e:
            messagebox.showerror("Error", str(e))

    # ============================================================
    # ADD TEAM
    # ============================================================

    def build_teams(self):
        f = self.team_tab

        labels = [
            "Team Name",
            "HLTV Rank",
            "VRS Rank",
            "Win Rate",
            "Player1 ID",
            "Player2 ID",
            "Player3 ID",
            "Player4 ID",
            "Player5 ID",
        ]

        self.team_entries = []

        for lab in labels:
            row = ttk.Frame(f)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=lab, width=20).pack(side="left")
            e = ttk.Entry(row)
            e.pack(side="left", expand=True, fill="x")
            self.team_entries.append(e)

        ttk.Button(
            f,
            text="Add Team",
            command=self.submit_team
        ).pack(pady=10)

    def submit_team(self):
        try:
            vals = [e.get() for e in self.team_entries]

            name = vals[0]
            hltv_rank = int(vals[1])
            vrs_rank = int(vals[2])
            win_rate = float(vals[3])

            player_ids = [
                int(vals[4]),
                int(vals[5]),
                int(vals[6]),
                int(vals[7]),
                int(vals[8]),
            ]

            add_or_update_team(
                name=name,
                hltv_rank=hltv_rank,
                vrs_rank=vrs_rank,
                win_rate=win_rate,
                player_ids=player_ids,
            )

            messagebox.showinfo("Success", "Team added/updated.")

        except Exception as e:
            messagebox.showerror("Error", str(e))
