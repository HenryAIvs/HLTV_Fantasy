# app.py
from player_db import ensure_schema as ensure_player_schema
from team_db import ensure_team_schema
from ui.ui_main import MainUI


# Make sure the database tables exist before launching the UI
def init_database():
    try:
        ensure_player_schema()
        ensure_team_schema()
        print("Database initialized.")
    except Exception as e:
        print("Database initialization error:", e)


if __name__ == "__main__":
    init_database()
    MainUI().mainloop()
