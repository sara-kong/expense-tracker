#!/usr/bin/env python3
"""
xpense.py — a tiny CLI expense tracker with CSV storage + per-category budgets.
Usage examples are at the bottom (or run: python xpense.py -h).
"""

from __future__ import annotations
import argparse, csv, json, os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Iterable

# --- UX helpers (pretty output via Rich, with safe fallback) ---
try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    _console = Console(force_terminal=True)  # force color/borders
    _has_rich = True
except ImportError:
    _has_rich = False
    Console = Table = box = None  # type: ignore

def print_table(headers, rows, title=None):
    """Pretty table if Rich is available; otherwise plain text."""
    if _has_rich:
        t = Table(*headers, title=title, box=box.ROUNDED, show_header=True, header_style="bold")
        for r in rows:
            t.add_row(*[str(x) for x in r])
        _console.print(t)
    else:
        if title:
            print(title)
        print(" | ".join(headers))
        print("-" * (len(" | ".join(headers)) + 4))
        for r in rows:
            print(" | ".join(str(x) for x in r))


# ---------- Storage ----------
DATA_DIR = Path(os.getenv("XPENSE_DATA_DIR", "data"))  # overridable for tests
EXPENSES_CSV = DATA_DIR / "expenses.csv"
BUDGETS_JSON = DATA_DIR / "budgets.json"

DATE_FMT = "%Y-%m-%d"

def ensure_storage():
    DATA_DIR.mkdir(exist_ok=True)
    if not EXPENSES_CSV.exists():
        with open(EXPENSES_CSV, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "amount", "category", "note"])
    if not BUDGETS_JSON.exists():
        BUDGETS_JSON.write_text(json.dumps({}, indent=2))

# ---------- Model ----------
@dataclass
class Expense:
    when: date
    amount: float
    category: str
    note: str = ""

    @classmethod
    def from_row(cls, row: Dict[str,str]) -> "Expense":
        return cls(
            when=datetime.strptime(row["date"], DATE_FMT).date(),
            amount=float(row["amount"]),
            category=row["category"],
            note=row.get("note", "")
        )

    def to_row(self) -> List[str]:
        return [self.when.strftime(DATE_FMT), f"{self.amount:.2f}", self.category, self.note]

# ---------- IO ----------
def load_expenses() -> List[Expense]:
    ensure_storage()
    out = []
    with open(EXPENSES_CSV, newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                out.append(Expense.from_row(row))
            except Exception:
                continue
    return out

def append_expense(e: Expense) -> None:
    ensure_storage()
    with open(EXPENSES_CSV, "a", newline="") as f:
        w = csv.writer(f)
        w.writerow(e.to_row())

def load_budgets() -> Dict[str, float]:
    ensure_storage()
    try:
        return json.loads(BUDGETS_JSON.read_text() or "{}")
    except Exception:
        return {}

def save_budgets(budgets: Dict[str, float]) -> None:
    ensure_storage()
    BUDGETS_JSON.write_text(json.dumps(budgets, indent=2))

# ---------- Helpers ----------
def parse_date(d: Optional[str]) -> date:
    if d is None:
        return date.today()
    return datetime.strptime(d, DATE_FMT).date()

def month_bounds(d: date) -> tuple[date, date]:
    start = d.replace(day=1)
    if start.month == 12:
        end = start.replace(year=start.year+1, month=1, day=1) - timedelta(days=1)
    else:
        end = start.replace(month=start.month+1, day=1) - timedelta(days=1)
    return start, end

def filter_expenses(expenses: Iterable[Expense],
                    start: Optional[date]=None,
                    end: Optional[date]=None,
                    category: Optional[str]=None) -> List[Expense]:
    out = []
    for e in expenses:
        if start and e.when < start: continue
        if end and e.when > end: continue
        if category and e.category.lower() != category.lower(): continue
        out.append(e)
    return out

def summarize(expenses: Iterable[Expense]) -> Dict[str, float]:
    totals = defaultdict(float)
    for e in expenses:
        totals[e.category] += e.amount
    return dict(totals)

def fmt_money(x: float) -> str:
    return f"${x:,.2f}"

# ---------- Commands ----------
def cmd_add(args):
    when = parse_date(args.date)
    e = Expense(when=when, amount=args.amount, category=args.category, note=args.note or "")
    append_expense(e)
    print(f"Added {fmt_money(e.amount)} to '{e.category}' on {e.when} — {e.note}")

    # Budget alert
    budgets = load_budgets()
    if e.category in budgets:
        start, end = month_bounds(when)
        mtd = sum(x.amount for x in filter_expenses(load_expenses(), start, end, e.category))
        budget = budgets[e.category]
        if mtd > budget:
            over = mtd - budget
            pct = (mtd / budget) * 100 if budget else 0
            print(f"⚠️  Budget exceeded for '{e.category}': {fmt_money(mtd)} / {fmt_money(budget)} ({pct:.0f}%, over by {fmt_money(over)})")
        else:
            left = budget - mtd
            pct = (mtd / budget) * 100 if budget else 0
            print(f"✅ MTD for '{e.category}': {fmt_money(mtd)} / {fmt_money(budget)} ({pct:.0f}%). Remaining: {fmt_money(left)}.")

def cmd_list(args):
    expenses = load_expenses()
    start = parse_date(args.start) if args.start else None
    end = parse_date(args.end) if args.end else None
    if args.month:
        if args.month == "this":
            start, end = month_bounds(date.today())
        else:
            y, m = args.month.split("-")
            start = date(int(y), int(m), 1)
            _, end = month_bounds(start)
    ex = filter_expenses(expenses, start, end, args.category)
    if not ex:
        print("No matching expenses.")
        return
    ex.sort(key=lambda x: (x.when, x.category))
    total = sum(e.amount for e in ex)
    rows = []
    show_ids = bool(getattr(args, "with_id", False))
    # compute row ids deterministically
    from collections import defaultdict as _dd
    all_rows = []
    for e in load_expenses():
        all_rows.append((e.when, e.amount, e.category, e.note))
    bucket = _dd(list)
    for idx, tup in enumerate(all_rows, 1):
        bucket[tup].append(idx)
    def ridx_for(e):
        return bucket[(e.when, e.amount, e.category, e.note)].pop(0)

    for e in ex:
        note = f"{e.note}" if e.note else ""
        base = [e.when, f"{fmt_money(e.amount):>10}", e.category, note]
        if show_ids:
            rows.append([ridx_for(e)] + base)
        else:
            rows.append(base)

    headers = (["ID","Date","Amount","Category","Note"] if show_ids
               else ["Date","Amount","Category","Note"])
    print_table(headers, rows, title=f"{len(ex)} expenses — Total {fmt_money(total)}")


def cmd_summary(args):
    expenses = load_expenses()
    today = date.today()
    if args.period == "today":
        start, end = today, today
    elif args.period == "week":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
    elif args.period == "month":
        start, end = month_bounds(today)
    elif args.period == "range":
        if not args.start or not args.end:
            print("Provide --start YYYY-MM-DD and --end YYYY-MM-DD for 'range'.")
            return
        start, end = parse_date(args.start), parse_date(args.end)
    else:
        start = end = None

    ex = filter_expenses(expenses, start, end, args.category)
    totals = summarize(ex)
    if not totals:
        print("No data for that period/filter.")
        return
    grand = sum(totals.values())
    print(f"Summary ({args.period}){f' for {args.category}' if args.category else ''}: {fmt_money(grand)} total")
    print("-"*48)
    for cat, amt in sorted(totals.items(), key=lambda kv: -kv[1]):
        pct = (amt / grand) * 100 if grand else 0
        print(f"{cat:<15} {fmt_money(amt):>12}   {pct:5.1f}%")

def cmd_set_budget(args):
    budgets = load_budgets()
    if args.amount is None or args.category is None:
        if not budgets:
            print("No budgets set.")
            return
        print("Budgets (per month):")
        print("-"*28)
        for cat, amt in sorted(budgets.items()):
            print(f"{cat:<15} {fmt_money(amt):>12}")
        return
    budgets[args.category] = round(float(args.amount), 2)
    save_budgets(budgets)
    print(f"Set budget for '{args.category}' to {fmt_money(budgets[args.category])}")

def cmd_report(args):
    today = date.today()
    start, end = month_bounds(today)
    expenses = filter_expenses(load_expenses(), start, end, None)
    totals = summarize(expenses)
    budgets = load_budgets()

    cats = sorted(set(list(totals.keys()) + list(budgets.keys())))
    if not cats:
        print("No data/budgets yet.")
        return
        
    rows = []
    for c in cats:
        spent = totals.get(c, 0.0)
        budget = budgets.get(c, 0.0)
        left = budget - spent if budget else 0.0

        if _has_rich and budget > 0:
            if left > 0:
                spent_str = f"[green]{fmt_money(spent)}[/green]"
                left_str = f"[green]{fmt_money(left)}[/green]"
            elif left == 0:
                spent_str = f"[yellow]{fmt_money(spent)}[/yellow]"
                left_str = f"[yellow]{fmt_money(left)}[/yellow]"
            else:
                spent_str = f"[red]{fmt_money(spent)}[/red]"
                left_str = f"[red]{fmt_money(left)}[/red]"
        else:
            spent_str = fmt_money(spent)
            left_str = fmt_money(left)

        rows.append([c, fmt_money(spent), fmt_money(budget), fmt_money(left)])

    print_table(["Category","Spent","Budget","Left(+)/Over(-)"], rows,
                title=f"Report — {today.strftime('%B %Y')} (MTD)")


# ---------- CLI ----------
def build_parser():
    p = argparse.ArgumentParser(description="Tiny CLI expense tracker.")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="Add an expense")
    a.add_argument("amount", type=float, help="Amount, e.g., 12.50")
    a.add_argument("category", type=str, help="Category, e.g., food")
    a.add_argument("-d", "--date", type=str, help="YYYY-MM-DD (default: today)")
    a.add_argument("-n", "--note", type=str, help="Optional note")
    a.set_defaults(func=cmd_add)

    l = sub.add_parser("list", help="List expenses (filters optional)")
    l.add_argument("-m", "--month", type=str, help='"this" or YYYY-MM (e.g., 2025-11)')
    l.add_argument("-s", "--start", type=str, help="YYYY-MM-DD")
    l.add_argument("-e", "--end", type=str, help="YYYY-MM-DD")
    l.add_argument("-c", "--category", type=str, help="Filter by category")
    l.add_argument("--with-id", action="store_true", help="Show row numbers")
    l.set_defaults(func=cmd_list)

    s = sub.add_parser("summary", help="Totals by category")
    s.add_argument("period", choices=["today", "week", "month", "range", "all"], help="Pick a period")
    s.add_argument("-s", "--start", type=str, help="YYYY-MM-DD (for 'range')")
    s.add_argument("-e", "--end", type=str, help="YYYY-MM-DD (for 'range')")
    s.add_argument("-c", "--category", type=str, help="Optional: filter to a single category")
    s.set_defaults(func=cmd_summary)

    b = sub.add_parser("set-budget", help="Set or view per-category monthly budgets")
    b.add_argument("category", nargs="?", help="Category name (omit to view)")
    b.add_argument("amount", nargs="?", type=float, help="Budget amount (omit to view)")
    b.set_defaults(func=cmd_set_budget)

    r = sub.add_parser("report", help="Month-to-date report vs budgets")
    r.set_defaults(func=cmd_report)

    return p

def main(argv=None):
    ensure_storage()
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)

if __name__ == "__main__":
    main()