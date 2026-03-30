"""Usage: python scripts/reset_episode_error.py <guid-prefix> — clear error, set downloaded."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: reset_episode_error.py <guid-prefix>", file=sys.stderr)
        sys.exit(2)
    prefix = sys.argv[1]
    db = Path(__file__).resolve().parents[1] / "data" / "bilradio.sqlite"
    con = sqlite3.connect(db)
    cur = con.execute(
        "UPDATE episodes SET status = 'downloaded', error = NULL "
        "WHERE guid LIKE ? AND status = 'error'",
        (f"{prefix}%",),
    )
    print(f"updated rows: {con.total_changes}")
    con.commit()
    con.close()

if __name__ == "__main__":
    main()
