# ui/ui_bracket.py

import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
import json
from typing import Dict, List, Tuple, Optional

from swiss_stage.swiss_models import TeamState
from swiss_stage.pairing import generate_pairings
from swiss_stage.swiss_round import get_match_type
from booster_mc import mc_estimate_round0_evs

DB_PATH = "fantasy_players.db"

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


class BracketTab:
    """
    Interactive Swiss bracket + Fantasy Booster Planner.

    - Bracket:
        * Loads teams from DB as TeamState objects.
        * Uses generate_pairings to compute pairings each round.
        * Lets you pick winners and commit results.
        * Shows standings (Qualified / Eliminated / In play).

    - Fantasy Booster Planner:
        * Select your fantasy players (ideally 5).
        * Tick which boosters you've already used.
        * Click "Run Booster MC for this round".
        * Runs MC from current state, shared pool of remaining boosters,
          BO1 vs BO3-aware, global greedy allocation per path.
        * Suggests a single consistent assignment: which booster (if any)
          to put on each player this round.
    """

    def __init__(self, parent):
        self.frame = ttk.Frame(parent)

        self.team_states: Dict[int, TeamState] = {}
        self.team_players: Dict[int, List[int]] = {}
        self.player_team_id: Dict[int, int] = {}

        self.round_number: int = 1
        self.current_pairings: List[Tuple[TeamState, TeamState]] = []
        self.winner_vars: List[tk.IntVar] = []

        # Fantasy planner state
        self.player_ids_by_label: Dict[str, int] = {}
        self.booster_used_vars: Dict[int, tk.BooleanVar] = {}

        self._build_ui()
        self._load_teams()
        self._render_standings()
        self._load_fantasy_players()

    # ============================================================
    # UI BUILD
    # ============================================================

    def _build_ui(self):
        f = self.frame

        # Top bar
        top = ttk.Frame(f)
        top.pack(fill="x", pady=5)

        self.round_label = ttk.Label(top, text="Round: 1", font=("Arial", 12, "bold"))
        self.round_label.pack(side="left", padx=5)

        ttk.Button(top, text="Start / Next Round", command=self.start_next_round).pack(side="left", padx=10)

        self.commit_button = ttk.Button(
            top, text="Commit Results", command=self.commit_results, state="disabled"
        )
        self.commit_button.pack(side="left", padx=10)

        # Main area: left (pairings/standings), right (planner)
        main = ttk.Frame(f)
        main.pack(fill="both", expand=True, padx=5, pady=5)

        # Left side: pairings + standings
        left = ttk.Frame(main)
        left.pack(side="left", fill="both", expand=True)

        self.pairings_frame = ttk.LabelFrame(left, text="Current Round Pairings")
        self.pairings_frame.pack(fill="x", padx=5, pady=5)

        standings_frame = ttk.LabelFrame(left, text="Standings")
        standings_frame.pack(fill="both", expand=True, padx=5, pady=5)

        self.standings_tree = ttk.Treeview(
            standings_frame,
            columns=("team", "wins", "losses", "status"),
            show="headings",
            height=10,
        )
        self.standings_tree.heading("team", text="Team")
        self.standings_tree.heading("wins", text="Wins")
        self.standings_tree.heading("losses", text="Losses")
        self.standings_tree.heading("status", text="Status")
        self.standings_tree.column("team", width=200)
        self.standings_tree.column("wins", width=60, anchor="center")
        self.standings_tree.column("losses", width=60, anchor="center")
        self.standings_tree.column("status", width=120, anchor="center")
        self.standings_tree.pack(fill="both", expand=True, padx=5, pady=5)

        # Right side: Fantasy Booster Planner
        right = ttk.LabelFrame(main, text="Fantasy Booster Planner")
        right.pack(side="left", fill="both", expand=True, padx=5, pady=5)

        ttk.Label(
            right,
            text="Select your fantasy players (ideally 5) from the list below."
        ).pack(anchor="w", padx=5, pady=(5, 0))

        list_frame = ttk.Frame(right)
        list_frame.pack(fill="x", padx=5, pady=5)

        self.fantasy_player_listbox = tk.Listbox(list_frame, selectmode="extended", width=40, height=8)
        self.fantasy_player_listbox.pack(side="left", fill="y")

        fp_sb = ttk.Scrollbar(list_frame, command=self.fantasy_player_listbox.yview)
        fp_sb.pack(side="right", fill="y")
        self.fantasy_player_listbox.configure(yscrollcommand=fp_sb.set)

        # Booster usage tick-boxes
        booster_frame = ttk.LabelFrame(right, text="Boosters already used")
        booster_frame.pack(fill="x", padx=5, pady=5)

        # We'll arrange 3 columns of 6 boosters each
        row_frames = [ttk.Frame(booster_frame) for _ in range(3)]
        for rf in row_frames:
            rf.pack(side="left", fill="y", padx=5, pady=2)

        for bid in range(18):
            col = bid // 6
            row_f = row_frames[col]
            var = tk.BooleanVar(value=False)
            self.booster_used_vars[bid] = var
            name = BOOSTER_NAMES.get(str(bid), f"Booster {bid}")
            cb = ttk.Checkbutton(row_f, text=name, variable=var)
            cb.pack(anchor="w")

        # MC controls
        mc_frame = ttk.Frame(right)
        mc_frame.pack(fill="x", padx=5, pady=5)

        ttk.Label(mc_frame, text="MC paths:").pack(side="left")
        self.mc_paths_var = tk.StringVar(value="2000")
        ttk.Entry(mc_frame, textvariable=self.mc_paths_var, width=8).pack(side="left", padx=5)

        ttk.Label(mc_frame, text="Workers:").pack(side="left")
        self.mc_workers_var = tk.StringVar(value="4")
        ttk.Entry(mc_frame, textvariable=self.mc_workers_var, width=4).pack(side="left", padx=5)

        ttk.Button(
            right,
            text="Run Booster MC for this round",
            command=self.run_booster_mc_for_round
        ).pack(pady=5, padx=5, anchor="w")

        # Output text
        self.planner_output = tk.Text(right, width=60, height=14)
        self.planner_output.pack(fill="both", expand=True, padx=5, pady=5)
        po_sb = ttk.Scrollbar(right, command=self.planner_output.yview)
        po_sb.pack(side="right", fill="y")
        self.planner_output.configure(yscrollcommand=po_sb.set)

    # ============================================================
    # LOAD TEAMS & PLAYERS
    # ============================================================

    def _load_teams(self):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT team_id, name, vrs_rank, hltv_rank, "
                "player1_id, player2_id, player3_id, player4_id, player5_id "
                "FROM teams ORDER BY vrs_rank"
            ).fetchall()
        finally:
            conn.close()

        self.team_states.clear()
        self.team_players.clear()
        self.player_team_id.clear()

        for r in rows:
            tid = r["team_id"]
            vrs = r["vrs_rank"]
            if vrs is None:
                vrs = r["hltv_rank"] or tid

            ts = TeamState(
                team_id=tid,
                vrs_rank=int(vrs),
                players={},  # not used here
            )
            self.team_states[tid] = ts

            pids = []
            for col in ["player1_id", "player2_id", "player3_id", "player4_id", "player5_id"]:
                pid = r[col]
                if pid is not None and pid != 0:
                    pid_int = int(pid)
                    pids.append(pid_int)
                    self.player_team_id[pid_int] = tid
            self.team_players[tid] = pids

    def _load_fantasy_players(self):
        self.fantasy_player_listbox.delete(0, tk.END)
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
            self.fantasy_player_listbox.insert(tk.END, label)
            self.player_ids_by_label[label] = pid

    # ============================================================
    # PAIRINGS & STANDINGS
    # ============================================================

    def _render_pairings(self):
        for child in self.pairings_frame.winfo_children():
            child.destroy()

        self.winner_vars = []

        if not self.current_pairings:
            ttk.Label(
                self.pairings_frame,
                text="No pairings yet. Click 'Start / Next Round'."
            ).pack(padx=5, pady=5)
            return

        for idx, (A, B) in enumerate(self.current_pairings):
            row = ttk.Frame(self.pairings_frame)
            row.pack(fill="x", pady=2, padx=5)

            label = ttk.Label(
                row,
                text=f"{A.team_id}: {self._team_name(A.team_id)}  vs  "
                     f"{B.team_id}: {self._team_name(B.team_id)}",
                width=60,
                anchor="w"
            )
            label.pack(side="left")

            var = tk.IntVar(value=0)
            self.winner_vars.append(var)

            rb_a = ttk.Radiobutton(
                row,
                text=f"Win {self._team_name(A.team_id)}",
                variable=var,
                value=A.team_id
            )
            rb_b = ttk.Radiobutton(
                row,
                text=f"Win {self._team_name(B.team_id)}",
                variable=var,
                value=B.team_id
            )
            rb_a.pack(side="left", padx=10)
            rb_b.pack(side="left", padx=10)

    def _render_standings(self):
        for item in self.standings_tree.get_children():
            self.standings_tree.delete(item)

        teams = list(self.team_states.values())
        teams.sort(key=lambda t: (-t.wins, t.vrs_rank))

        for t in teams:
            if t.wins >= 3:
                status = "Qualified"
            elif t.losses >= 3:
                status = "Eliminated"
            else:
                status = "In play"
            self.standings_tree.insert(
                "", "end",
                values=(self._team_name(t.team_id), t.wins, t.losses, status)
            )

    def _team_name(self, team_id: int) -> str:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT name FROM teams WHERE team_id = ?", (team_id,)
            ).fetchone()
            if row:
                return row["name"]
        finally:
            conn.close()
        return f"Team {team_id}"

    def _group_pools(self) -> Dict[Tuple[int, int], List[TeamState]]:
        pools: Dict[Tuple[int, int], List[TeamState]] = {}
        for t in self.team_states.values():
            if t.wins >= 3 or t.losses >= 3:
                continue
            key = (t.wins, t.losses)
            pools.setdefault(key, []).append(t)
        return pools

    def start_next_round(self):
        if self.current_pairings and any(var.get() == 0 for var in self.winner_vars):
            if not messagebox.askyesno(
                "Uncommitted results",
                "Some matches have no winner selected. "
                "Proceed anyway and discard these pairings?"
            ):
                return

        pools = self._group_pools()
        all_pairings: List[Tuple[TeamState, TeamState]] = []

        for key, pool in pools.items():
            if len(pool) < 2:
                continue
            if len(pool) % 2 != 0:
                messagebox.showwarning(
                    "Odd pool size",
                    f"Pool {key} has an odd number of teams ({len(pool)})."
                )
            pool_sorted = sorted(pool, key=lambda tt: tt.vrs_rank)
            pairings = generate_pairings(pool_sorted)
            all_pairings.extend(pairings)

        if not all_pairings:
            messagebox.showinfo("Done", "No more pairings can be generated (all teams are done).")
            return

        self.current_pairings = all_pairings
        self.commit_button.config(state="normal")
        self._render_pairings()

    def commit_results(self):
        if not self.current_pairings:
            messagebox.showerror("Error", "No pairings to commit. Start a round first.")
            return

        if len(self.current_pairings) != len(self.winner_vars):
            messagebox.showerror("Error", "Internal error: mismatch in pairings/winner vars.")
            return

        for i, var in enumerate(self.winner_vars):
            if var.get() == 0:
                messagebox.showerror("Error", f"Match {i+1} has no winner selected.")
                return

        for (A, B), var in zip(self.current_pairings, self.winner_vars):
            winner_id = var.get()
            if winner_id == A.team_id:
                winner, loser = A, B
            else:
                winner, loser = B, A

            winner.wins += 1
            loser.losses += 1
            winner.opponents_played.add(loser.team_id)
            loser.opponents_played.add(winner.team_id)

        self.current_pairings = []
        self.winner_vars = []
        for child in self.pairings_frame.winfo_children():
            child.destroy()
        self.commit_button.config(state="disabled")

        self.round_number += 1
        self.round_label.config(text=f"Round: {self.round_number}")

        self._render_standings()
        messagebox.showinfo("Round committed", "Results applied. You can now generate the next round.")

    # ============================================================
    # FANTASY BOOSTER MONTE CARLO
    # ============================================================

    def run_booster_mc_for_round(self):
        self.planner_output.delete("1.0", tk.END)

        # Fantasy players
        sel_labels = [self.fantasy_player_listbox.get(i)
                      for i in self.fantasy_player_listbox.curselection()]
        if not sel_labels:
            self.planner_output.insert(tk.END, "No fantasy players selected.\n")
            return

        fantasy_players = [self.player_ids_by_label[label] for label in sel_labels]
        if len(fantasy_players) > 5:
            self.planner_output.insert(
                tk.END,
                f"Warning: you selected {len(fantasy_players)} players. "
                "We will still simulate all of them, but a fantasy lineup is usually 5.\n\n"
            )

        # Paths & workers
        try:
            n_paths = int(self.mc_paths_var.get().strip())
        except Exception:
            n_paths = 2000
        if n_paths <= 0:
            n_paths = 2000

        try:
            n_workers = int(self.mc_workers_var.get().strip())
        except Exception:
            n_workers = 4
        if n_workers <= 0:
            n_workers = 4

        # Available boosters from checkboxes
        available_boosters = [
            bid for bid, var in self.booster_used_vars.items()
            if not var.get()
        ]

        self.planner_output.insert(
            tk.END,
            f"Running booster Monte Carlo: {n_paths} paths, {n_workers} workers, "
            f"{len(available_boosters)}/{len(self.booster_used_vars)} boosters available...\n"
        )
        self.planner_output.update_idletasks()

        # team_states_init & vrs_ranks
        team_states_init = self.team_states.copy()
        vrs_ranks = {tid: ts.vrs_rank for tid, ts in self.team_states.items()}
        player_team = dict(self.player_team_id)

        # player_booster_triggers: pid -> {booster_id -> triggerRate}
        player_booster_triggers: Dict[int, Dict[int, float]] = {}
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT player_id, boosters_json FROM players"
            ).fetchall()
            for r in rows:
                pid = r["player_id"]
                bj = r["boosters_json"]
                if not bj:
                    continue
                try:
                    obj = json.loads(bj)
                except Exception:
                    obj = {}
                if not isinstance(obj, dict):
                    continue
                player_booster_triggers[pid] = {
                    int(bid): float(val)
                    for bid, val in obj.items()
                    if float(val) > 0.0 and int(bid) in available_boosters
                }
        finally:
            conn.close()

        # Call MC engine
        round0_evs = mc_estimate_round0_evs(
            n_paths=n_paths,
            team_states_init=team_states_init,
            vrs_ranks=vrs_ranks,
            fantasy_players=fantasy_players,
            player_team=player_team,
            player_booster_triggers=player_booster_triggers,
            n_workers=n_workers,
            available_boosters=available_boosters,
        )

        if not round0_evs:
            self.planner_output.insert(
                tk.END,
                "No EVs computed (no paths or no boosters with non-zero triggerRate).\n"
            )
            return

        # Build edges & assignment
        edges = []
        for (pid, bid), ev in round0_evs.items():
            if pid not in fantasy_players:
                continue
            if ev <= 0.0:
                continue
            edges.append((ev, pid, bid))

        if not edges:
            self.planner_output.insert(
                tk.END,
                "Optimizer suggests NO boosters this round (all EVs <= 0).\n"
            )
            return

        edges.sort(reverse=True, key=lambda x: x[0])

        assigned: Dict[int, int] = {}
        used_boosters = set()
        total_assigned_ev = 0.0

        for ev, pid, bid in edges:
            if pid in assigned:
                continue
            if bid in used_boosters:
                continue
            assigned[pid] = bid
            used_boosters.add(bid)
            total_assigned_ev += ev

        self.planner_output.insert(
            tk.END,
            "\nBest booster assignment for this round (shared pool, "
            f"{n_paths} MC paths):\n\n"
        )
        self.planner_output.insert(
            tk.END,
            f"Total expected booster points this round: {total_assigned_ev:.3f}\n\n"
        )

        def _player_name(pid: int) -> str:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT name FROM players WHERE player_id = ?",
                    (pid,)
                ).fetchone()
                if row:
                    return row["name"]
            finally:
                conn.close()
            return f"Player {pid}"

        for pid in fantasy_players:
            name = _player_name(pid)
            if pid in assigned:
                bid = assigned[pid]
                booster_name = BOOSTER_NAMES.get(str(bid), f"Booster {bid}")
                ev = round0_evs.get((pid, bid), 0.0)
                self.planner_output.insert(
                    tk.END,
                    f"{name}: use '{booster_name}' (id={bid}), "
                    f"EV ≈ {ev:.3f} this round.\n"
                )
            else:
                self.planner_output.insert(
                    tk.END,
                    f"{name}: no booster recommended this round.\n"
                )

        self.planner_output.insert(
            tk.END,
            "\nTick boosters as 'used' once you spend them so future rounds "
            "respect the remaining pool.\n"
        )
        self.planner_output.insert(
            tk.END,
            "(See terminal for booster MC progress logs.)\n"
        )
        self.planner_output.update_idletasks()
