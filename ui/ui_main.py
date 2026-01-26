import tkinter as tk
from tkinter import ttk, messagebox

from ui.ui_import_triggerrates import TriggerRatesImportTab
from ui.ui_view import ViewTab
from ui.ui_add import AddTab
from ui.ui_edit import EditTab
from ui.ui_simulation import SimulationTab
from ui.ui_import_chromium import HLTVImportTab
from ui.ui_import_ratings import RatingsImportTab
from ui.ui_bracket import BracketTab         
from db_admin import wipe_database



class MainUI(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("CS Fantasy Toolkit")
        self.geometry("1300x850")

        # -------------------------------
        # TOP BAR with Wipe DB button
        # -------------------------------
        top_bar = ttk.Frame(self)
        top_bar.pack(fill="x")

        ttk.Label(
            top_bar,
            text="CS Fantasy Toolkit",
            font=("Arial", 14, "bold")
        ).pack(side="left", padx=10)

        ttk.Button(
            top_bar,
            text="WIPE DATABASE",
            command=self.confirm_wipe,
            style="Danger.TButton"
        ).pack(side="right", padx=10, pady=5)

        # -------------------------------
        # MAIN NOTEBOOK (tabs)
        # -------------------------------
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

        # Create tabs
        view = ViewTab(nb)
        add = AddTab(nb)
        edit = EditTab(nb)
        sim = SimulationTab(nb)
        hltv_import = HLTVImportTab(nb)
        trigger_import = TriggerRatesImportTab(nb)
        ratings_tab = RatingsImportTab(nb)
        bracket_tab = BracketTab(nb)       

        nb.add(view.frame, text="View Database")
        nb.add(add.frame, text="Add Entries")
        nb.add(edit.frame, text="Edit/Delete Entries")
        nb.add(sim.frame, text="Run Simulation")
        nb.add(hltv_import.frame, text="HLTV Import")
        nb.add(trigger_import.frame, text="Trigger Rates Import")
        nb.add(ratings_tab.frame, text="Ratings Import")
        nb.add(bracket_tab.frame, text="Bracket")  

    # ============================================================
    # WIPE DATABASE CONFIRMATION
    # ============================================================

    def confirm_wipe(self):
        ok = messagebox.askyesno(
            "Confirm Wipe",
            "Are you sure you want to WIPE the entire database?\n"
            "This will delete ALL players and ALL teams.\n\n"
            "This action cannot be undone."
        )
        if not ok:
            return

        try:
            wipe_database()
            messagebox.showinfo("Success", "Database has been wiped clean.")
        except Exception as e:
            messagebox.showerror("Wipe Error", str(e))


if __name__ == "__main__":
    MainUI().mainloop()
