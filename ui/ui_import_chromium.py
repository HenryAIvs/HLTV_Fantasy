import tkinter as tk
from tkinter import ttk, messagebox
from playwright.sync_api import sync_playwright
from player_db import add_or_update_player
from team_db import add_or_update_team


class HLTVImportTab:
    def __init__(self, parent):
        self.frame = ttk.Frame(parent)
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

        # (player_id, name, cost, team_name, rating)
        self.players = []
        # team_name -> [player_ids]
        self.teams = {}

        self.build()

    # ============================================================
    # UI BUILD
    # ============================================================

    def build(self):
        f = self.frame

        ttk.Label(
            f,
            text="HLTV Fantasy Import (JSON-based)",
            font=("Arial", 12, "bold")
        ).pack(pady=10)

        # Event ID input
        row = ttk.Frame(f)
        row.pack(pady=5)
        ttk.Label(row, text="Event ID:", width=12).pack(side="left")
        self.event_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.event_var, width=25).pack(side="left")

        ttk.Button(f, text="Fetch Players", command=self.fetch_players).pack(pady=10)
        ttk.Button(f, text="Import To DB", command=self.import_to_db).pack(pady=5)

        # Output window
        self.output = tk.Text(f, width=100, height=35)
        self.output.pack(pady=10)

        sb = ttk.Scrollbar(f, command=self.output.yview)
        sb.pack(side="right", fill="y")
        self.output.configure(yscrollcommand=sb.set)

    # ============================================================
    # FETCH FANTASY JSON (NO SCRAPING)
    # ============================================================

    def fetch_players(self):
        event_id = self.event_var.get().strip()
        if not event_id.isdigit():
            messagebox.showerror("Error", "Event ID must be numeric.")
            return

        self.output.delete(1.0, tk.END)
        self.output.insert(tk.END, "Fetching HLTV Fantasy Data...\n")

        try:
            if not self.playwright:
                self.playwright = sync_playwright().start()

            self.browser = self.playwright.chromium.launch(headless=False)
            self.context = self.browser.new_context()
            self.page = self.context.new_page()

            url = f"https://www.hltv.org/fantasy/{event_id}/leagues/create"
            self.page.goto(url, timeout=0)

            # Accept cookies if present
            try:
                self.page.locator("button:has-text('Allow all cookies')").click(timeout=2500)
            except:
                pass

            # Fetch Fantasy JSON directly
            data = self.page.evaluate(
                """
                async () => {
                    const jsonUrl = window.location.pathname.replace(/\\/leagues\\/create.*/, '/leagues/create/json');
                    const res = await fetch(jsonUrl, { headers: { accept: 'application/json' }});
                    return await res.json();
                }
                """
            )

            money = data.get("moneyDraftData", {})
            players_list = []
            teams_dict = {}

            for team_block in money.get("teams", []):
                team_name = team_block["teamData"]["name"]
                teams_dict.setdefault(team_name, [])

                for entry in team_block.get("players", []):
                    p_data = entry["playerData"]
                    pid = int(p_data["fantasyPlayerId"]["playerId"])
                    name = p_data["name"]
                    cost = int(entry["cost"])
                    rating = float(p_data.get("stats", {}).get("rating", 1.00))

                    players_list.append((pid, name, cost, team_name, rating))
                    teams_dict[team_name].append(pid)

            self.players = players_list
            self.teams = teams_dict

            self.output.insert(tk.END, f"Fetched {len(players_list)} players.\n\n")
            for pid, name, cost, team, rating in players_list:
                self.output.insert(
                    tk.END,
                    f"{pid} — {name} — ${cost} — {team} — Rating {rating}\n"
                )

        except Exception as e:
            messagebox.showerror("Error", str(e))
        finally:
            try:
                if self.context:
                    self.context.close()
                if self.browser:
                    self.browser.close()
            except:
                pass
            self.context = None
            self.browser = None
            self.page = None

    # ============================================================
    # IMPORT TEAMS + PLAYERS INTO DB
    # ============================================================

    def import_to_db(self):
        if not self.players:
            messagebox.showerror("Error", "Fetch players first.")
            return

        try:
            self.output.insert(tk.END, "\n=== Importing Teams ===\n")

            # Import teams
            for team_name, pids in self.teams.items():
                ids = pids[:5] if len(pids) >= 5 else pids + [0] * (5 - len(pids))
                add_or_update_team(
                    name=team_name,
                    hltv_rank=999,
                    vrs_rank=999,
                    win_rate=0.5,
                    player_ids=ids,
                )
                self.output.insert(tk.END, f"Team saved: {team_name}\n")

            self.output.insert(tk.END, "\n=== Importing Players ===\n")

            # Import players (boosters/roles will be filled by triggerRates importer)
            for pid, name, cost, team_name, rating in self.players:
                add_or_update_player(
                    player_id=pid,
                    name=name,
                    rating=rating,
                    price=cost,
                    best_role="",
                    major_win_pct=0.0,
                    minor_win_pct=0.0,
                    boosters_json=None,
                    roles_json=None,
                )

                self.output.insert(
                    tk.END,
                    f"Saved {name} — Rating {rating}\n"
                )

            messagebox.showinfo("Success", "Players & Teams imported successfully.")

        except Exception as e:
            messagebox.showerror("DB Error", str(e))
