import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "shorts.db"


def main():
    if not DB_PATH.exists():
        print("No shorts.db found yet. Run main.py first to create the clip history.")
        return
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT fingerprint, source_path, output_path, processed_at FROM clips "
        "WHERE status='rendered' ORDER BY processed_at ASC"
    ).fetchall()
    if not rows:
        print("No rendered clips waiting for review.")
        return

    print("\nRendered clips awaiting review (these source clips will never be reused):")
    for i, (_, source, output, processed) in enumerate(rows, 1):
        exists = bool(output and Path(output).exists())
        print(f"{i}. [{'READY' if exists else 'OUTPUT MISSING'}] {Path(source).name}\n   Output: {output}\n   Rendered: {processed}")
    print("\nEnter a number to reject that render, A to reject all, or Q to quit.")
    choice = input("> ").strip().lower()
    if choice == "q" or not choice:
        return
    selected = rows if choice == "a" else None
    if selected is None:
        try:
            idx = int(choice) - 1
            if idx < 0 or idx >= len(rows):
                raise ValueError
            selected = [rows[idx]]
        except ValueError:
            print("Invalid selection.")
            return

    action = input("Move rejected outputs to 'rejected' (M) or permanently delete them (D)? [M/D]: ").strip().lower()
    if action not in {"m", "d"}:
        print("Cancelled; choose M or D.")
        return

    rejected_dir = ROOT / "rejected"
    if action == "m":
        rejected_dir.mkdir(exist_ok=True)

    for fp, source, output, processed in selected:
        out = Path(output) if output else None
        if out and out.exists():
            if action == "m":
                destination = rejected_dir / out.name
                counter = 1
                while destination.exists():
                    destination = rejected_dir / f"{out.stem}_{counter}{out.suffix}"
                    counter += 1
                shutil.move(str(out), str(destination))
                print(f"Moved to rejected: {destination.name}")
            else:
                out.unlink()
                print(f"Deleted: {out.name}")
        con.execute(
            "UPDATE clips SET status='rejected', error=? WHERE fingerprint=?",
            ("Rejected by user during review; source clip is permanently excluded from automatic processing.", fp)
        )
    con.commit()
    con.close()
    print("Done. Rejected source clips will not be rendered or uploaded again.")


if __name__ == "__main__":
    main()
