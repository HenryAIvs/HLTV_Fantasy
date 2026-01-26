import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3

from player_db import add_or_update_player, get_player
from team_db import add_or_update_team

DB_PATH = "fantasy_players.db"


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


class EditTab:
    def __init__(self, parent):
        self.frame = ttk.Frame(parent)
        self.build()

    def build(self):
        nb = ttk.Notebook(self.frame)
        nb.pack(fill="both", expand=True)

        self.player_tab = ttk.Frame(nb)
        self.team_tab = ttk.Frame(nb)

        nb.add(self.player_tab, text="Edit Players")
        nb.add(self.team_tab, text="Edit Teams")

        self.build_players()
        self.build_teams()

    # ============================================================
    # PLAYERS
    # ============================================================

    def build_players(self):
        f = self.player_tab

        ttk.Label(f, text="Select Player").pack(pady=5)

        self.edit_player_var = tk.StringVar()
        self.edit_player_combo = ttk.Combobox(f, textvariable=self.edit_player_var, width=40)
        self.edit_player_combo.pack(pady=5)

        self.refresh_edit_player_dropdown()

        ttk.Button(f, text="Load Player", command=self.load_player_for_edit).pack(pady=5)

        labels = [
            "Name",
            "Rating",
            "Price",
            "Best Role",
            "Major Win %",
            "Minor Win %",
        ]
        self.edit_player_entries = []

        for lab in labels:
            row = ttk.Frame(f)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=lab, width=20).pack(side="left")
            e = ttk.Entry(row)
            e.pack(side="left", fill="x", expand=True)
            self.edit_player_entries.append(e)

        ttk.Label(
            f,
            text="(Leave a field blank to keep its current value)",
            foreground="grey"
        ).pack(pady=5)

        ttk.Button(f, text="Update Player", command=self.update_player).pack(pady=10)
        ttk.Button(f, text="Delete Player", command=self.delete_player).pack(pady=5)

    def refresh_edit_player_dropdown(self):
        conn = _connect()
        try:
            rows = conn.execute("SELECT player_id, name FROM players ORDER BY player_id").fetchall()
        finally:
            conn.close()

        items = [f"{r['player_id']}: {r['name']}" for r in rows]
        self.edit_player_combo["values"] = items

    def load_player_for_edit(self):
        key = self.edit_player_var.get()
        if not key:
            return
        pid = int(key.split(":")[0])

        row = get_player(pid)
        if not row:
            messagebox.showerror("Error", f"Player {pid} not found.")
            return

        # Fill entries (so you can see current values)
        vals = [
            row.get("name", ""),
            row.get("rating", ""),
            row.get("price", ""),
            row.get("best_role", ""),
            row.get("major_win_pct", ""),
            row.get("minor_win_pct", ""),
        ]

        for entry, val in zip(self.edit_player_entries, vals):
            entry.delete(0, tk.END)
            entry.insert(0, str(val))

    def update_player(self):
        key = self.edit_player_var.get()
        if not key:
            messagebox.showerror("Error", "Select a player first.")
            return
        pid = int(key.split(":")[0])

        existing = get_player(pid)
        if not existing:
            messagebox.showerror("Error", f"Player {pid} not found in DB.")
            return

        # Current values from DB
        curr_name = existing.get("name", "")
        curr_rating = float(existing.get("rating", 0.0))
        curr_price = int(existing.get("price", 0))
        curr_best_role = existing.get("best_role", "")
        curr_major = float(existing.get("major_win_pct", 0.0))
        curr_minor = float(existing.get("minor_win_pct", 0.0))
        curr_boosters_json = existing.get("boosters_json")
        curr_roles_json = existing.get("roles_json")

        # New entries (blank means "keep old value")
        e_name, e_rating, e_price, e_best_role, e_major, e_minor = [
            e.get().strip() for e in self.edit_player_entries
        ]

        new_name = e_name if e_name != "" else curr_name
        new_rating = curr_rating
        if e_rating != "":
            try:
                new_rating = float(e_rating)
            except ValueError:
                messagebox.showerror("Error", "Rating must be a number.")
                return

        new_price = curr_price
        if e_price != "":
            try:
                new_price = int(e_price)
            except ValueError:
                messagebox.showerror("Error", "Price must be an integer.")
                return

        new_best_role = e_best_role if e_best_role != "" else curr_best_role

        new_major = curr_major
        if e_major != "":
            try:
                new_major = float(e_major)
            except ValueError:
                messagebox.showerror("Error", "Major Win % must be a number.")
                return

        new_minor = curr_minor
        if e_minor != "":
            try:
                new_minor = float(e_minor)
            except ValueError:
                messagebox.showerror("Error", "Minor Win % must be a number.")
                return

        try:
            add_or_update_player(
                player_id=pid,
                name=new_name,
                rating=new_rating,
                price=new_price,
                best_role=new_best_role,
                major_win_pct=new_major,
                minor_win_pct=new_minor,
                boosters_json=curr_boosters_json,
                roles_json=curr_roles_json,
            )
            messagebox.showinfo("Success", "Player updated.")
            self.refresh_edit_player_dropdown()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def delete_player(self):
        key = self.edit_player_var.get()
        if not key:
            return
        pid = int(key.split(":")[0])

        if not messagebox.askyesno("Confirm", f"Delete player {pid}?"):
            return

        conn = _connect()
        try:
            conn.execute("DELETE FROM players WHERE player_id = ?", (pid,))
            conn.commit()
        finally:
            conn.close()

        messagebox.showinfo("Deleted", f"Player {pid} deleted.")
        self.refresh_edit_player_dropdown()

    # ============================================================
    # TEAMS
    # ============================================================

    def build_teams(self):
        f = self.team_tab

        ttk.Label(f, text="Select Team").pack(pady=5)

        self.edit_team_var = tk.StringVar()
        self.edit_team_combo = ttk.Combobox(f, textvariable=self.edit_team_var, width=40)
        self.edit_team_combo.pack(pady=5)

        self.refresh_edit_team_dropdown()

        ttk.Button(f, text="Load Team", command=self.load_team_for_edit).pack(pady=5)

        labels = [
            "Name",
            "HLTV Rank",
            "VRS Rank",
            "Win Rate",
            "Player1 ID",
            "Player2 ID",
            "Player3 ID",
            "Player4 ID",
            "Player5 ID",
        ]
        self.edit_team_entries = []

        for lab in labels:
            row = ttk.Frame(f)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=lab, width=20).pack(side="left")
            e = ttk.Entry(row)
            e.pack(side="left", fill="x", expand=True)
            self.edit_team_entries.append(e)

        ttk.Label(
            f,
            text="(Leave a field blank to keep its current value)",
            foreground="grey"
        ).pack(pady=5)

        ttk.Button(f, text="Update Team", command=self.update_team).pack(pady=10)
        ttk.Button(f, text="Delete Team", command=self.delete_team).pack(pady=5)

    def refresh_edit_team_dropdown(self):
        conn = _connect()
        try:
            rows = conn.execute("SELECT team_id, name FROM teams ORDER BY team_id").fetchall()
        finally:
            conn.close()

        items = [f"{r['team_id']}: {r['name']}" for r in rows]
        self.edit_team_combo["values"] = items

    def load_team_for_edit(self):
        key = self.edit_team_var.get()
        if not key:
            return
        tid = int(key.split(":")[0])

        conn = _connect()
        try:
            r = conn.execute("SELECT * FROM teams WHERE team_id = ?", (tid,)).fetchone()
        finally:
            conn.close()

        if not r:
            messagebox.showerror("Error", f"Team {tid} not found.")
            return

        vals = [
            r["name"],
            r["hltv_rank"],
            r["vrs_rank"],
            r["win_rate"],
            r["player1_id"],
            r["player2_id"],
            r["player3_id"],
            r["player4_id"],
            r["player5_id"],
        ]

        for entry, val in zip(self.edit_team_entries, vals):
            entry.delete(0, tk.END)
            entry.insert(0, str(val))

    def update_team(self):
        key = self.edit_team_var.get()
        if not key:
            messagebox.showerror("Error", "Select a team first.")
            return
        tid = int(key.split(":")[0])

        conn = _connect()
        try:
            existing = conn.execute("SELECT * FROM teams WHERE team_id = ?", (tid,)).fetchone()
        finally:
            conn.close()

        if not existing:
            messagebox.showerror("Error", f"Team {tid} not found in DB.")
            return

        d = dict(existing)
        (
            e_name,
            e_hltv,
            e_vrs,
            e_winrate,
            e_p1,
            e_p2,
            e_p3,
            e_p4,
            e_p5,
        ) = [e.get().strip() for e in self.edit_team_entries]

        new_name = e_name if e_name != "" else d["name"]

        hltv_rank = d.get("hltv_rank", 999)
        if e_hltv != "":
            try:
                hltv_rank = int(e_hltv)
            except ValueError:
                messagebox.showerror("Error", "HLTV Rank must be an integer.")
                return

        vrs_rank = d.get("vrs_rank", 999)
        if e_vrs != "":
            try:
                vrs_rank = int(e_vrs)
            except ValueError:
                messagebox.showerror("Error", "VRS Rank must be an integer.")
                return

        win_rate = d.get("win_rate", 0.5)
        if e_winrate != "":
            try:
                win_rate = float(e_winrate)
            except ValueError:
                messagebox.showerror("Error", "Win Rate must be a number.")
                return

        def _coerce_player_id(val, curr):
            if val == "":
                return curr
            try:
                pid = int(val)
            except ValueError:
                raise ValueError("Player IDs must be integers.")
            return pid

        try:
            p1 = _coerce_player_id(e_p1, d.get("player1_id"))
            p2 = _coerce_player_id(e_p2, d.get("player2_id"))
            p3 = _coerce_player_id(e_p3, d.get("player3_id"))
            p4 = _coerce_player_id(e_p4, d.get("player4_id"))
            p5 = _coerce_player_id(e_p5, d.get("player5_id"))
        except ValueError as ve:
            messagebox.showerror("Error", str(ve))
            return

        try:
            add_or_update_team(
                name=new_name,
                hltv_rank=hltv_rank,
                vrs_rank=vrs_rank,
                win_rate=win_rate,
                player_ids=[p1, p2, p3, p4, p5],
            )
            messagebox.showinfo("Success", "Team updated.")
            self.refresh_edit_team_dropdown()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def delete_team(self):
        key = self.edit_team_var.get()
        if not key:
            return
        tid = int(key.split(":")[0])

        if not messagebox.askyesno("Confirm", f"Delete team {tid}?"):
            return

        conn = _connect()
        try:
            conn.execute("DELETE FROM teams WHERE team_id = ?", (tid,))
            conn.commit()
        finally:
            conn.close()

        messagebox.showinfo("Deleted", f"Team {tid} deleted.")
        self.refresh_edit_team_dropdown()
