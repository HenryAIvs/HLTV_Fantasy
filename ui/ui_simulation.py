import itertools
import json
import math
import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import matplotlib.pyplot as plt

from swiss_stage.fantasy_montecarlo import simulate_swiss_fantasy
from player_db import get_player
from role_assignment import best_role_assignment_for_team, extract_role_scores_for_player

DB_PATH = "fantasy_players.db"

# Booster and role names (match your JSON mappings)
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

# Try to import reportlab; if missing, we'll warn and skip PDF export
try:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False


class SimulationTab:
    def __init__(self, parent):
        self.frame = ttk.Frame(parent)
        self.team_map = {}
        self.team_name_by_id = {}
        self.player_meta_by_id = {}
        self.rows_for_graph: list[dict] = []
        self.last_results = None

        # For exclusion column in tree
        self.tree_player_id: dict[str, int] = {}  # tree item -> player_id
        self.excluded_player_ids: set[int] = set()

        self.build()

    # ============================================================
    # UI BUILD
    # ============================================================

    def build(self):
        f = self.frame

        ttk.Label(f, text="Swiss Simulation", font=("Arial", 12, "bold")).pack(pady=10)

        controls = ttk.Frame(f)
        controls.pack(pady=5, fill="x")

        ttk.Label(controls, text="BO Mode:").pack(side="left", padx=(0, 5))
        self.bo_var = tk.StringVar(value="elim_qual")
        ttk.OptionMenu(controls, self.bo_var, "elim_qual", "elim_qual", "all", "none").pack(side="left")

        ttk.Label(controls, text="  Sims:").pack(side="left", padx=(15, 5))
        self.sims_var = tk.StringVar(value="400")
        ttk.Entry(controls, textvariable=self.sims_var, width=8).pack(side="left")

        ttk.Button(controls, text="Run Simulation", command=self.run_simulation).pack(side="left", padx=10)
        ttk.Button(controls, text="Find Best 5 Team", command=self.find_best_team).pack(side="left", padx=5)
        ttk.Button(controls, text="Show Graph", command=self.show_graph).pack(side="left", padx=5)

        ttk.Label(f, text="Select Teams").pack(pady=5)
        self.team_listbox = tk.Listbox(f, selectmode="multiple", width=45, height=10)
        self.team_listbox.pack()

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT team_id, name FROM teams ORDER BY team_id").fetchall()
        conn.close()

        for r in rows:
            tid = r["team_id"]
            name = r["name"]
            label = f"{tid}: {name}"
            self.team_listbox.insert(tk.END, label)
            self.team_map[label] = tid
            self.team_name_by_id[tid] = name

        self.status = tk.Text(f, width=100, height=7)
        self.status.pack(pady=5)
        status_sb = ttk.Scrollbar(f, command=self.status.yview)
        status_sb.pack(side="right", fill="y")
        self.status.configure(yscrollcommand=status_sb.set)

        # Add an "Exclude" column at the end
        self.tree = ttk.Treeview(
            f,
            columns=(
                "team", "player", "rating", "cost",
                "total", "rating_pts", "win_pts", "role_pts", "booster_pts",
                "exclude",
            ),
            show="headings",
            height=15,
        )
        self.tree.pack(fill="both", expand=True, pady=5)

        headers = [
            ("team", "Team"),
            ("player", "Player"),
            ("rating", "Rating"),
            ("cost", "Cost"),
            ("total", "Total pts"),
            ("rating_pts", "Rating pts"),
            ("win_pts", "Win pts"),
            ("role_pts", "Role pts"),
            ("booster_pts", "Booster pts"),
            ("exclude", "Exclude"),
        ]
        for col, label in headers:
            self.tree.heading(col, text=label)
            if col == "exclude":
                self.tree.column(col, width=70, anchor="center")
            else:
                self.tree.column(col, width=110, anchor="center")

        # Bind click to toggle exclude column
        self.tree.bind("<Button-1>", self._on_tree_click)

    # ============================================================
    # TREE CLICK HANDLER FOR EXCLUDE COLUMN
    # ============================================================

    def _on_tree_click(self, event):
        """
        If the user clicks in the 'exclude' column of a row, toggle its state
        between '☐' and '☑' and update self.excluded_player_ids accordingly.
        """
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return

        col_id = self.tree.identify_column(event.x)  # e.g. '#1', '#2', ...
        # columns: team(1), player(2), rating(3), cost(4), total(5),
        # rating_pts(6), win_pts(7), role_pts(8), booster_pts(9), exclude(10)
        if col_id != "#10":
            return

        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return

        # Get current values, toggle last one
        values = list(self.tree.item(row_id, "values"))
        if not values:
            return

        current = values[-1]
        new_val = "☐" if current == "☑" else "☑"
        values[-1] = new_val
        self.tree.item(row_id, values=values)

        # Update excluded_player_ids
        pid = self.tree_player_id.get(row_id)
        if pid is None:
            return

        if new_val == "☑":
            self.excluded_player_ids.add(pid)
        else:
            self.excluded_player_ids.discard(pid)

    # ============================================================
    # HELPERS
    # ============================================================

    @staticmethod
    def _parse_json(text):
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            return None

    @staticmethod
    def _compute_expected_games(team_result):
        p30 = team_result.get("3-0", 0.0)
        p31 = team_result.get("3-1", 0.0)
        p32 = team_result.get("3-2", 0.0)
        p23 = team_result.get("2-3", 0.0)
        p13 = team_result.get("1-3", 0.0)
        p03 = team_result.get("0-3", 0.0)

        p3 = p30 + p03
        p4 = p31 + p13
        p5 = p32 + p23

        return 3.0 * p3 + 4.0 * p4 + 5.0 * p5

    def _compute_booster_ev_from_json(self, boosters_json, expected_games: float) -> float:
        """
        boosters_json: { "0": value, "1": value, ..., "17": value }

        New rule (for team optimizer):
          - Consider ALL boosters the player has triggerRates for.
          - Compute the average triggerRate across those boosters.
          - Booster EV ≈ 5 * avg_triggerRate * expected_games.
        """
        obj = self._parse_json(boosters_json)
        if not isinstance(obj, dict) or not obj:
            return 0.0

        rates = []
        for v in obj.values():
            try:
                fv = float(v)
            except Exception:
                continue
            if fv < 0.0:
                continue
            rates.append(fv)

        if not rates:
            return 0.0

        avg_rate = sum(rates) / len(rates)
        eg = max(0.0, float(expected_games))
        return 5.0 * avg_rate * eg

    def _load_player_meta(self):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT player_id, name, rating, price, boosters_json, roles_json FROM players"
            ).fetchall()
            meta = {}
            for r in rows:
                meta[r["player_id"]] = {
                    "name": r["name"],
                    "rating": r["rating"],
                    "price": r["price"],
                    "boosters_json": r["boosters_json"],
                    "roles_json": r["roles_json"],
                }
            self.player_meta_by_id = meta
        finally:
            conn.close()

    # ============================================================
    # RUN SIMULATION
    # ============================================================

    def run_simulation(self):
        selected = [self.team_listbox.get(i) for i in self.team_listbox.curselection()]
        if len(selected) < 2:
            messagebox.showerror("Error", "Select at least 2 teams.")
            return

        try:
            n_sims = int(self.sims_var.get().strip())
            if n_sims <= 0:
                raise ValueError
        except Exception:
            messagebox.showerror("Error", "Number of simulations must be a positive integer.")
            return

        team_ids = [self.team_map[s] for s in selected]
        bo_mode = self.bo_var.get()

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        vrs_ranks = {}
        try:
            for tid in team_ids:
                row = conn.execute("SELECT * FROM teams WHERE team_id = ?", (tid,)).fetchone()
                if row is None:
                    vrs = 999
                else:
                    d = dict(row)
                    vrs = d.get("vrs_rank", 999)
                vrs_ranks[tid] = vrs
        finally:
            conn.close()

        self.status.delete("1.0", tk.END)
        self.status.insert("1.0", f"Running Monte Carlo (sims={n_sims}, mode={bo_mode})...\n")

        results = simulate_swiss_fantasy(
            team_ids=team_ids,
            vrs_ranks=vrs_ranks,
            bo3_mode=bo_mode,
            n_sims=n_sims,
        )
        self.last_results = results

        self._load_player_meta()

        # Reset exclusion state
        self.tree_player_id.clear()
        self.excluded_player_ids.clear()

        self.status.insert(tk.END, "Done Monte Carlo.\n\nTeam distributions:\n")
        for tid in team_ids:
            team_name = self.team_name_by_id.get(tid, f"Team {tid}")
            r = results.get(tid, {})
            self.status.insert(
                tk.END,
                f"{team_name} (id {tid}): 3-0={r.get('3-0',0):.3f}, 3-1={r.get('3-1',0):.3f}, "
                f"3-2={r.get('3-2',0):.3f}, 2-3={r.get('2-3',0):.3f}, "
                f"1-3={r.get('1-3',0):.3f}, 0-3={r.get('0-3',0):.3f}\n"
            )

        self.tree.delete(*self.tree.get_children())
        self.rows_for_graph.clear()

        for tid in team_ids:
            team_name = self.team_name_by_id.get(tid, f"Team {tid}")
            r = results.get(tid, {})
            players_data = r.get("players", {})

            for pid, comps in players_data.items():
                pid_int = int(pid)
                pmeta = self.player_meta_by_id.get(pid_int) or get_player(pid_int) or {}
                rating = pmeta.get("rating", 0.0)
                name = pmeta.get("name", f"Player {pid_int}")
                cost = pmeta.get("price", 0)

                total = comps.get("total", 0.0)
                rating_pts = comps.get("rating", 0.0)
                win_pts = comps.get("win", 0.0)
                role_pts = comps.get("role", 0.0)
                booster_pts = comps.get("booster", 0.0)

                row_vals = [
                    team_name,
                    name,
                    f"{float(rating):.2f}",
                    cost,
                    f"{total:.2f}",
                    f"{rating_pts:.2f}",
                    f"{win_pts:.2f}",
                    f"{role_pts:.2f}",
                    f"{booster_pts:.2f}",
                    "☐",  # exclude column initially unchecked
                ]
                item_id = self.tree.insert("", "end", values=row_vals)
                self.tree_player_id[item_id] = pid_int  # map row to player_id

                self.rows_for_graph.append({
                    "team": team_name,
                    "player": name,
                    "rating": float(rating),
                    "cost": cost,
                    "total": total,
                    "rating_pts": rating_pts,
                    "win_pts": win_pts,
                    "role_pts": role_pts,
                    "booster_pts": booster_pts,
                })

    # ============================================================
    # FIND BEST TEAM (5 players, ≤1M, ≤2 per team, no role clashes)
    # ============================================================

    def find_best_team(self):
        if not self.last_results:
            messagebox.showerror("Error", "Run the simulation first.")
            return

        BUDGET = 1_000_000
        MAX_PER_TEAM = 2

        players_info = []

        expected_games_by_team = {
            tid: self._compute_expected_games(res)
            for tid, res in self.last_results.items()
        }

        for tid, team_res in self.last_results.items():
            team_name = self.team_name_by_id.get(tid, f"Team {tid}")
            EG = expected_games_by_team.get(tid, 0.0)
            players_data = team_res.get("players", {})

            for pid_key, comps in players_data.items():
                pid = int(pid_key)
                # skip excluded players
                if pid in self.excluded_player_ids:
                    continue

                pmeta = self.player_meta_by_id.get(pid)
                if not pmeta:
                    continue

                name = pmeta["name"]
                price = pmeta["price"]
                boosters_json = pmeta.get("boosters_json", "")
                roles_json = pmeta.get("roles_json", "")

                rating_ev = float(comps.get("rating", 0.0))
                win_ev = float(comps.get("win", 0.0))
                role_ev = float(comps.get("role", 0.0))
                booster_ev = self._compute_booster_ev_from_json(boosters_json, EG)

                total_ev = rating_ev + win_ev + role_ev + booster_ev

                players_info.append({
                    "player_id": pid,
                    "name": name,
                    "team_id": tid,
                    "team_name": team_name,
                    "price": price,
                    "rating_ev": rating_ev,
                    "win_ev": win_ev,
                    "role_ev": role_ev,
                    "booster_ev": booster_ev,
                    "total_ev": total_ev,
                    "boosters_json": boosters_json,
                    "roles_json": roles_json,
                })

        if len(players_info) < 5:
            messagebox.showerror("Error", "Not enough players with MC + boosters data (after exclusions).")
            return

        n_players = len(players_info)
        try:
            total_combos = math.comb(n_players, 5)
        except AttributeError:
            from math import factorial
            total_combos = factorial(n_players) // (factorial(5) * factorial(n_players - 5))

        self.status.insert(
            tk.END,
            f"\n[Optimizer] Considering {n_players} players -> {total_combos:,} 5-man combinations.\n"
        )
        self.status.update_idletasks()

        role_scores_by_player: dict[int, dict[int, float]] = {}
        best_role_score_by_player: dict[int, float] = {}

        for p in players_info:
            pid = p["player_id"]
            row = get_player(pid)
            if row:
                scores = extract_role_scores_for_player(row)
            else:
                scores = {}
            role_scores_by_player[pid] = scores
            best_role_score_by_player[pid] = max(scores.values()) if scores else 0.0

        for p in players_info:
            p["base_no_role_ev"] = p["total_ev"] - p["role_ev"]

        valid_teams: list[tuple[float, list[dict], dict[int, int]]] = []

        processed = 0
        kept = 0
        skip_budget = 0
        skip_team = 0
        skip_roles = 0
        skip_bound = 0

        top_scores: list[float] = []

        for combo in itertools.combinations(players_info, 5):
            processed += 1

            total_cost = sum(p["price"] for p in combo)
            if total_cost > BUDGET:
                skip_budget += 1
                continue

            counts: dict[int, int] = {}
            valid = True
            for p in combo:
                counts[p["team_id"]] = counts.get(p["team_id"], 0) + 1
                if counts[p["team_id"]] > MAX_PER_TEAM:
                    valid = False
                    break
            if not valid:
                skip_team += 1
                continue

            base_sum = sum(p["base_no_role_ev"] for p in combo)
            upper_role_sum = sum(best_role_score_by_player[p["player_id"]] for p in combo)
            upper_bound = base_sum + upper_role_sum

            if len(top_scores) >= 10:
                current_threshold = min(top_scores)
                if upper_bound <= current_threshold:
                    skip_bound += 1
                    continue

            player_ids = [p["player_id"] for p in combo]
            assignment, _role_score = best_role_assignment_for_team(player_ids, role_scores_by_player)
            if assignment is None:
                skip_roles += 1
                continue

            total_ev = sum(p["total_ev"] for p in combo)
            valid_teams.append((total_ev, list(combo), assignment))
            kept += 1

            if len(top_scores) < 10:
                top_scores.append(total_ev)
            else:
                worst_current = min(top_scores)
                if total_ev > worst_current:
                    worst_index = top_scores.index(worst_current)
                    top_scores[worst_current == min(top_scores)] = total_ev

        if not valid_teams:
            self.status.insert(
                tk.END,
                f"\n[Optimizer] Finished. No valid team under constraints.\n"
            )
            self.status.update_idletasks()
            messagebox.showinfo(
                "No valid team",
                "No team satisfied budget / per-team / role-clash constraints."
            )
            return

        valid_teams.sort(key=lambda x: x[0], reverse=True)
        top_n = min(10, len(valid_teams))
        top_teams = valid_teams[:top_n]

        self.status.insert(
            tk.END,
            f"\n[Optimizer] Finished. Processed {processed:,} combos; "
            f"Kept {kept:,} valid teams; "
            f"Skipped (budget/team/roles/bound) = "
            f"{skip_budget}/{skip_team}/{skip_roles}/{skip_bound}\n"
        )
        self.status.insert(
            tk.END,
            f"=== Top {top_n} 5-Man Teams (MC + boosters, no role clashes) ===\n"
        )
        self.status.update_idletasks()

        top_teams_for_pdf = []

        for idx, (team_ev, combo, assignment) in enumerate(top_teams, start=1):
            total_cost = sum(p["price"] for p in combo)
            self.status.insert(tk.END, f"\n#{idx} — Total EV: {team_ev:.2f}, Cost: {total_cost}\n")

            team_rows = []
            for p in sorted(combo, key=lambda x: -x["total_ev"]):
                pid = p["player_id"]
                assigned_role_idx = assignment.get(pid)
                if assigned_role_idx is not None:
                    role_id_str = str(assigned_role_idx)
                    role_name = ROLE_NAMES.get(role_id_str, f"Role {role_id_str}")
                else:
                    role_name = "-"

                line_summary = {
                    "name": p["name"],
                    "team_name": p["team_name"],
                    "price": p["price"],
                    "total_ev": p["total_ev"],
                    "rating_ev": p["rating_ev"],
                    "win_ev": p["win_ev"],
                    "role_ev": p["role_ev"],
                    "booster_ev": p["booster_ev"],
                    "role_name": role_name,
                }
                team_rows.append(line_summary)

                self.status.insert(
                    tk.END,
                    f"  {p['name']:16} | Team: {p['team_name']:<12} | "
                    f"Cost: {p['price']:7} | EV total: {p['total_ev']:7.2f} "
                    f"(R {p['rating_ev']:6.2f} / W {p['win_ev']:6.2f} / "
                    f"role {p['role_ev']:6.2f} / boost {p['booster_ev']:6.2f})\n"
                )
                self.status.insert(
                    tk.END,
                    f"      Assigned Role: {role_name}\n"
                )

            top_teams_for_pdf.append({
                "rank": idx,
                "team_ev": team_ev,
                "total_cost": total_cost,
                "players": team_rows,
            })

        self.status.update_idletasks()
        self._export_top_teams_to_pdf(top_teams_for_pdf)

    # ============================================================
    # PDF EXPORT
    # ============================================================

    def _export_top_teams_to_pdf(self, top_teams: list[dict]):
        if not REPORTLAB_AVAILABLE:
            messagebox.showwarning(
                "PDF export not available",
                "The 'reportlab' package is not installed.\n"
                "Run 'pip install reportlab' in your environment to enable PDF export."
            )
            return

        pdf_path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
            title="Save Top 10 Teams as PDF",
        )
        if not pdf_path:
            return

        c = canvas.Canvas(pdf_path, pagesize=letter)
        width, height = letter

        y = height - 50
        c.setFont("Helvetica-Bold", 14)
        c.drawString(50, y, "Top Teams — Swiss Fantasy Optimiser")
        y -= 30

        c.setFont("Helvetica", 10)
        for team in top_teams:
            if y < 100:
                c.showPage()
                y = height - 50
                c.setFont("Helvetica", 10)

            header = f"#{team['rank']} — Total EV: {team['team_ev']:.2f}, Cost: {team['total_cost']}"
            c.drawString(50, y, header)
            y -= 15

            for p in team["players"]:
                if y < 80:
                    c.showPage()
                    y = height - 50
                    c.setFont("Helvetica", 10)

                line1 = (
                    f"{p['name']} | Team: {p['team_name']} | Cost: {p['price']} | "
                    f"EV: {p['total_ev']:.2f}"
                )
                c.drawString(60, y, line1)
                y -= 12

                line2 = (
                    f"Rating: {p['rating_ev']:.2f}, Win: {p['win_ev']:.2f}, "
                    f"Role: {p['role_ev']:.2f}, Booster: {p['booster_ev']:.2f}, "
                    f"Assigned Role: {p['role_name']}"
                )
                c.drawString(70, y, line2)
                y -= 14

            y -= 8  # extra space between teams

        c.save()
        messagebox.showinfo("PDF saved", f"Top teams PDF saved to:\n{pdf_path}")

    # ============================================================
    # GRAPH: total vs cost
    # ============================================================

    def show_graph(self):
        if not self.rows_for_graph:
            messagebox.showerror("Error", "Run a simulation first.")
            return

        costs = [r["cost"] for r in self.rows_for_graph]
        totals = [r["total"] for r in self.rows_for_graph]
        labels = [f"{r['player']} ({r['team']})" for r in self.rows_for_graph]

        if not costs:
            messagebox.showerror("Error", "No player data available for graph.")
            return

        plt.figure(figsize=(8, 6))
        plt.scatter(costs, totals)
        plt.xlabel("Cost")
        plt.ylabel("Expected Fantasy Points")
        plt.title("Player Expected Points vs Cost")

        top_indices = sorted(range(len(totals)), key=lambda i: totals[i], reverse=True)[:5]
        for i in top_indices:
            plt.annotate(labels[i], (costs[i], totals[i]), fontsize=8, xytext=(5, 5),
                         textcoords="offset points")

        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.show()
