# ui/ui_test_roles.py

import json
import math
import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox

from role_assignment import best_role_assignment_for_team, extract_role_scores_for_player
from player_db import get_player

DB_PATH = "fantasy_players.db"

# Same role name mapping as elsewhere (our indices 0..11)
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


class RoleTestTab:
    def __init__(self, parent):
        self.frame = ttk.Frame(parent)
        self.player_listbox = None
        self.output = None
        self.player_ids_by_label = {}
        self.build()

    def build(self):
        f = self.frame

        ttk.Label(
            f,
            text="Role Tester (pick 5 players and see role assignment)",
            font=("Arial", 12, "bold")
        ).pack(pady=10)

        main_frame = ttk.Frame(f)
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # Left: player list
        left = ttk.Frame(main_frame)
        left.pack(side="left", fill="y", padx=5, pady=5)

        ttk.Label(left, text="Players (Ctrl/Shift-click to select 5)").pack(anchor="w")

        self.player_listbox = tk.Listbox(left, selectmode="extended", width=40, height=25)
        self.player_listbox.pack(side="left", fill="y")

        sb = ttk.Scrollbar(left, command=self.player_listbox.yview)
        sb.pack(side="right", fill="y")
        self.player_listbox.configure(yscrollcommand=sb.set)

        ttk.Button(
            left,
            text="Test Role Assignment",
            command=self.test_roles
        ).pack(pady=10)

        # Right: output
        right = ttk.Frame(main_frame)
        right.pack(side="left", fill="both", expand=True, padx=5, pady=5)

        ttk.Label(right, text="Role scores and assigned roles").pack(anchor="w")

        self.output = tk.Text(right, width=100, height=30)
        self.output.pack(fill="both", expand=True)

        out_sb = ttk.Scrollbar(right, command=self.output.yview)
        out_sb.pack(side="right", fill="y")
        self.output.configure(yscrollcommand=out_sb.set)

        self.load_players()

    def load_players(self):
        """
        Load all players from DB and populate the listbox.
        """
        self.player_listbox.delete(0, tk.END)
        self.player_ids_by_label.clear()

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT player_id, name, rating, price FROM players ORDER BY name"
            ).fetchall()
        finally:
            conn.close()

        for r in rows:
            pid = r["player_id"]
            name = r["name"]
            rating = r["rating"]
            price = r["price"]
            label = f"{pid:4d} — {name:16s} | rating={rating:.2f} | cost={price}"
            self.player_listbox.insert(tk.END, label)
            self.player_ids_by_label[label] = pid

    def test_roles(self):
        """
        Take the selected players (must be 5), run best_role_assignment_for_team,
        and display a breakdown of each player's role scores and assigned role.
        """
        sel = [self.player_listbox.get(i) for i in self.player_listbox.curselection()]
        if len(sel) != 5:
            messagebox.showerror(
                "Select 5 players",
                f"Please select exactly 5 players (you selected {len(sel)})."
            )
            return

        player_ids = [self.player_ids_by_label[label] for label in sel]

        # Compute assignment
        assignment, total_role_score = best_role_assignment_for_team(player_ids)

        self.output.delete("1.0", tk.END)

        if assignment is None:
            self.output.insert(
                tk.END,
                "No clash-free role assignment possible for these players.\n"
            )
            return

        self.output.insert(
            tk.END,
            f"Best role assignment (no clashes) for players {player_ids}:\n"
        )
        self.output.insert(
            tk.END,
            f"Total role score (sum of per-match role EVs): {total_role_score:.3f}\n\n"
        )

        # For each player, show their role scores and which one was assigned
        for pid in player_ids:
            row = get_player(pid)
            if not row:
                self.output.insert(tk.END, f"Player {pid} not found in DB.\n\n")
                continue

            name = row.get("name", f"Player {pid}")
            roles_json_str = row.get("roles_json") or "{}"
            try:
                roles_obj = json.loads(roles_json_str)
            except Exception:
                roles_obj = {}

            self.output.insert(
                tk.END,
                f"Player {pid} — {name}\n"
            )

            # All role scores
            role_scores = extract_role_scores_for_player(row)
            assigned_role_index = assignment.get(pid)

            # Show roles sorted by score
            for rid in sorted(role_scores.keys(), key=lambda r: role_scores[r], reverse=True):
                score = role_scores[rid]
                rid_str = str(rid)
                role_name = ROLE_NAMES.get(rid_str, f"Role {rid_str}")
                assigned_mark = "  (ASSIGNED)" if rid == assigned_role_index else ""
                self.output.insert(
                    tk.END,
                    f"  [{rid:2d}] {role_name:16s} -> score={score:6.3f}{assigned_mark}\n"
                )

            if assigned_role_index is None:
                self.output.insert(tk.END, "  (No role assigned for this player?)\n")

            self.output.insert(tk.END, "\n")
