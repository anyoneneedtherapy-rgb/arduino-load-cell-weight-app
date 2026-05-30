"""School timetable organizer — pure data logic + Tkinter UI."""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import calendar
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
import serial
import serial.tools.list_ports

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DAYS: tuple[tuple[str, str], ...] = (
    ("Monday", "🌙 "),
    ("Tuesday", "🔥 "),
    ("Wednesday", "💧 "),
    ("Thursday", "🌳 "),
    ("Friday", "⭐ "),
    ("Saturday", "🎉 "),
    ("Sunday", "😴 "),
)
DAY_NAMES = tuple(d for d, _ in DAYS)
DAY_EMOJI = {name: emoji for name, emoji in DAYS}
DEFAULT_DATA_FILE = "timetable_data.json"

# ---------------------------------------------------------------------------
# Pure helpers (no I/O, no globals)
# ---------------------------------------------------------------------------
def normalize_name(name: str) -> str:
    return " ".join(name.strip().split()).title()

def parse_day_label(combo_value: str) -> str:
    return combo_value.split(" ", 1)[-1]

def greeting_for_hour(hour: int) -> str:
    if hour < 12: return "Good Morning"
    if hour < 17: return "Good Afternoon"
    if hour < 20: return "Good Evening"
    return "Good Night"

def emoji_for_hour(hour: int) -> str:
    if hour < 5: return "🌙"
    if hour < 12: return "🌅"
    if hour < 17: return "☀️"
    if hour < 20: return "🌤️"
    return "🌙"

def migrate_timetable(raw: dict) -> dict[str, list[dict]]:
    """Normalize legacy dict-per-day saves into lists of class entries."""
    migrated: dict[str, list[dict]] = {}
    def as_class(item: dict) -> dict | None:
        if not isinstance(item, dict) or not item.get("name"):
            return None
        return {
            "name": item["name"],
            "period": item.get("period", " "),
            "textbooks": list(item.get("textbooks", [])),
        }

    for day, entry in raw.items():
        if isinstance(entry, list):
            migrated[day] = [c for c in map(as_class, entry) if c]
        elif isinstance(entry, dict):
            migrated[day] = [
                {"name": subject, "period": " ", "textbooks": list(books)}
                for subject, books in entry.items()
            ]
    return migrated

def parse_period_number(period_label: str) -> int | None:
    """Extract a numeric period from labels like 'Period 2'. Returns None if unknown."""
    parts = period_label.strip().split()
    if len(parts) >= 2 and parts[0].lower() == "period" and parts[1].isdigit():
        return int(parts[1])
    return None

def infer_period_count(timetable: dict[str, list[dict]], default: int = 6) -> int:
    """Infer max period number present in stored entries."""
    nums: list[int] = []
    for classes in timetable.values():
        for entry in classes:
            num = parse_period_number(entry.get("period", ""))
            if num is not None:
                nums.append(num)
    return max(nums) if nums else default

def total_class_count(timetable: dict[str, list]) -> int:
    return sum(len(classes) for classes in timetable.values())

def days_with_classes(timetable: dict[str, list]) -> list[str]:
    return [day for day in DAY_NAMES if timetable.get(day)]

def add_textbooks(
    textbook_list: list[str], names: list[str]
) -> tuple[list[str], int, int]:
    """Return (new_list, added_count, skipped_duplicates)."""
    existing = {b.lower() for b in textbook_list}
    updated = list(textbook_list)
    added = skipped = 0
    for raw in names:
        name = normalize_name(raw)
        if not name: continue
        key = name.lower()
        if key in existing:
            skipped += 1
            continue
        updated.append(name)
        existing.add(key)
        added += 1

    if added:
        updated.sort()
    return updated, added, skipped

def strip_textbook_from_timetable(timetable: dict[str, list], name: str) -> dict[str, list]:
    return {
        day: [
            {**entry, "textbooks": [b for b in entry["textbooks"] if b != name]}
            for entry in classes
        ]
        for day, classes in timetable.items()
    }

def make_class_entry(name: str, textbooks: list[str], period: str = "") -> dict:
    return {
        "name": normalize_name(name),
        "period": period.strip(),
        "textbooks": list(textbooks),
    }

def consolidate_classes_for_day(timetable: dict[str, list], day: str) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """Merge duplicate subjects on the same day into combined period labels."""
    classes = timetable.get(day, [])
    subject_data: dict[str, dict] = {}
    
    for i, entry in enumerate(classes):
        subj = normalize_name(entry.get("name", ""))
        period = entry.get("period", f"Period {i+1}")
        books = entry.get("textbooks", [])
        if not subj: continue

        if subj not in subject_data:
            subject_data[subj] = {"periods": [], "books": set()}
        subject_data[subj]["periods"].append(period)
        subject_data[subj]["books"].update(books)

    result_labels = []
    all_books = set()
    for subj in sorted(subject_data.keys()):
        data = subject_data[subj]
        periods = sorted(data["periods"], key=lambda p: int(p.split()[-1]) if p.split()[-1].isdigit() else 99)
        all_books.update(data["books"])
        p_nums = [p.split()[-1] for p in periods]
        if len(p_nums) == 1:
            label = f"Period {p_nums[0]}: {subj}"
        elif len(p_nums) == 2:
            label = f"Periods {p_nums[0]} & {p_nums[1]}: {subj}"
        else:
            label = f"Periods {', '.join(p_nums[:-1])} & {p_nums[-1]}: {subj}"
        result_labels.append((label, sorted(data["books"])))
        
    return result_labels, sorted(all_books)

def setup_status(school_year: str, textbook_list: list, timetable: dict) -> tuple[str, str]:
    n_days = len(days_with_classes(timetable))
    n_classes = total_class_count(timetable)
    year = school_year or "Not set"
    if n_classes == 0:
        step = "Start by adding classes to your timetable."
    else:
        step = f"Ready — {n_classes} class(es) across {n_days} day(s)."
    return year, step

def recommendations(
    now: datetime, textbook_list: list, timetable: dict
) -> list[str]:
    hour, weekday = now.hour, now.weekday()
    tips: list[str] = []
    if total_class_count(timetable) == 0:
        tips.append("Start by adding each class for your active school days. ")

    time_tips = (
        (6, 12, ["Check your timetable and packing list for today. "]),
        (12, 17, ["Afternoon: glance at tomorrow's schedule. "]),
        (17, 21, ["Evening: pack your bag for tomorrow. "]),
    )
    for start, end, messages in time_tips:
        if start <= hour < end:
            tips.extend(messages)
            if start == 6 and weekday < 5:
                tips.append("School day — pack the books you need! ")
            break
    else:
        tips.append("Night: make sure tomorrow's books are ready. ")

    if weekday >= 5:
        tips.append("Weekend — plan the week ahead. ")
    return tips

def format_packing_text(day: str, class_rows: list[tuple[str, list]], all_books: list[str], homework_due: list[dict], weights: dict[str, float]) -> str:
    parts = [f"PACKING LIST FOR {day.upper()}\n\n "]
    
    # Textbooks Section
    parts.append("TEXTBOOKS:\n ")
    for label, books in class_rows:
        parts.append(f"  {label}\n ")
        if books:
            parts.extend(f"    · {book}\n " for book in books)
        else:
            parts.append("    (none)\n ")
        parts.append("\n ")
    
    # Homework Section
    if homework_due:
        parts.append("\n-------------------------\n ")
        parts.append("HOMEWORK DUE:\n ")
        for item in homework_due:
            subject = item.get("subject", "Miscellaneous")
            task = item.get("task", " ")
            parts.append(f"  · {subject}: {task}\n ")
            hw_book = item.get("linked_book")
            if hw_book:
                parts.append(f"    (Bring: {hw_book})\n ")
    else:
        parts.append("\n-------------------------\n ")
        parts.append("No homework due.\n ")

    # Calculate total weight including 1000g buffer for lunch/water
    book_weight = sum(weights.get(b, 0) for b in all_books)
    total_weight = book_weight + 1000
    
    parts.append("\n=========================\n ")
    parts.append("TOTAL EXPECTED WEIGHT:\n ")
    parts.append(f"          {total_weight:.0f} g\n ")
    return "".join(parts)

# ---------------------------------------------------------------------------
# Data store (thin mutable shell around pure functions)
# ---------------------------------------------------------------------------
@dataclass
class TimetableStore:
    data_file: str = DEFAULT_DATA_FILE
    school_name: str = "My School"
    school_year: str = " "
    term_name: str = "Term 1"
    active_days: list[str] = field(default_factory=lambda: ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"])
    textbook_list: list[str] = field(default_factory=list)
    textbook_weights: dict[str, float] = field(default_factory=dict)  # NEW: Weights in grams
    subjects: list[str] = field(
        default_factory=lambda: [
            "Chinese", "English", "Mathematics", "Science", "Geography", "History", "Music", "Art",
        ]
    )
    # Maps textbook name -> subject name
    textbook_subject_map: dict[str, str] = field(default_factory=dict)
    # Legacy / current-week view (kept for backward compatibility within this file).
    timetable: dict[str, list[dict]] = field(default_factory=dict)
    # Multi-week storage: Week A, Week B, ...
    timetable_weeks: dict[str, dict[str, list[dict]]] = field(default_factory=dict)
    active_week: str = "Week A"
    period_counts: dict[str, int] = field(default_factory=dict)

    def load(self) -> None:
        if not os.path.exists(self.data_file):
            return
        try:
            with open(self.data_file, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return

        self.school_name = data.get("school_name", "My School")
        self.school_year = data.get("school_year", " ")
        self.term_name = data.get("term_name", "Term 1")
        loaded_days = data.get("active_days")
        self.active_days = list(loaded_days) if isinstance(loaded_days, list) and loaded_days else ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        self.textbook_list = list(data.get("textbook_list", []))
        
        # Load weights (default to 0 if missing)
        self.textbook_weights = {normalize_name(k): float(v) for k, v in data.get("textbook_weights", {}).items()}

        loaded_subjects = data.get("subjects")
        if isinstance(loaded_subjects, list) and loaded_subjects:
            self.subjects = [normalize_name(s) for s in loaded_subjects if normalize_name(s)]
        else:
            self.subjects = [normalize_name(s) for s in self.subjects if normalize_name(s)]
        self.subjects = sorted(set(self.subjects))

        self.textbook_subject_map = {
            normalize_name(book): normalize_name(subject)
            for book, subject in dict(data.get("textbook_subject_map", {}) or {}).items()
            if normalize_name(book) and normalize_name(subject)
        }

        if isinstance(data.get("timetable_weeks"), dict) and data.get("timetable_weeks"):
            self.timetable_weeks = {
                str(week): migrate_timetable(tt) for week, tt in data.get("timetable_weeks", {}).items()
            }
            self.active_week = str(data.get("active_week", next(iter(self.timetable_weeks), "Week A")))
            self.period_counts = dict(data.get("period_counts", {}) or {})
            if self.active_week not in self.timetable_weeks:
                self.active_week = next(iter(self.timetable_weeks), self.active_week)
            self.timetable = self.timetable_weeks.get(self.active_week, {})
        else:
            self.timetable = migrate_timetable(data.get("timetable", {}))
            self.timetable_weeks = {"Week A": self.timetable}
            self.active_week = "Week A"
            default_periods = infer_period_count(self.timetable, default=6)
            self.period_counts = {"Week A": default_periods}

        if not self.textbook_list and data.get("all_books"):
            self.textbook_list = sorted(data["all_books"])
        
        valid_books = set(self.textbook_list)
        valid_subjects = set(self.subjects)
        self.textbook_subject_map = {
            book: subject
            for book, subject in self.textbook_subject_map.items()
            if book in valid_books and subject in valid_subjects
        }
        # Clean up weights for removed books
        self.textbook_weights = {b: w for b, w in self.textbook_weights.items() if b in valid_books}

    def save(self) -> None:
        if self.active_week not in self.timetable_weeks:
            self.timetable_weeks[self.active_week] = {}
        self.timetable_weeks[self.active_week] = self.timetable

        payload = {
            "school_name": self.school_name,
            "school_year": self.school_year,
            "term_name": self.term_name,
            "active_days": self.active_days,
            "textbook_list": self.textbook_list,
            "subjects": self.subjects,
            "textbook_subject_map": self.textbook_subject_map,
            "textbook_weights": self.textbook_weights,  # Save weights
            "timetable": self.timetable,
            "timetable_weeks": self.timetable_weeks,
            "active_week": self.active_week,
            "period_counts": self.period_counts,
        }
        with open(self.data_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=4)

    def classes_on(self, day: str) -> list[dict]:
        return list(self.timetable.get(day, []))

    def add_textbook_names(self, names: list[str]) -> tuple[int, int]:
        new_list, added, skipped = add_textbooks(self.textbook_list, names)
        if added:
            self.textbook_list = new_list
            self.save()
        return added, skipped

    def remove_textbook(self, name: str) -> bool:
        if name not in self.textbook_list:
            return False
        self.textbook_list.remove(name)
        self.textbook_subject_map.pop(name, None)
        self.textbook_weights.pop(name, None)
        self.timetable = strip_textbook_from_timetable(self.timetable, name)
        self.save()
        return True

    def set_textbook_weight(self, name: str, weight: float) -> None:
        name = normalize_name(name)
        if name in self.textbook_list:
            self.textbook_weights[name] = weight
            self.save()

    def add_subject_name(self, name: str) -> bool:
        normalized = normalize_name(name)
        if not normalized or normalized in self.subjects:
            return False
        self.subjects.append(normalized)
        self.subjects.sort()
        self.save()
        return True

    def remove_subject_name(self, name: str) -> bool:
        normalized = normalize_name(name)
        if normalized not in self.subjects:
            return False
        self.subjects.remove(normalized)
        self.textbook_subject_map = {
            book: subject for book, subject in self.textbook_subject_map.items()
            if subject != normalized
        }
        self.save()
        return True

    def link_textbook_to_subject(self, textbook: str, subject: str) -> bool:
        textbook = normalize_name(textbook)
        subject = normalize_name(subject)
        if textbook not in self.textbook_list or subject not in self.subjects:
            return False
        self.textbook_subject_map[textbook] = subject
        self.save()
        return True

    def textbooks_for_subject(self, subject: str) -> list[str]:
        normalized = normalize_name(subject)
        return sorted([book for book in self.textbook_list if self.textbook_subject_map.get(book) == normalized])

    def set_school_year(self, year_label: str) -> None:
        self.school_year = year_label.strip()
        self.save()

    def set_school_profile(self, school_name: str, school_year: str, term_name: str, active_days: list[str]) -> tuple[bool, str]:
        chosen_days = [d for d in DAY_NAMES if d in active_days]
        if not chosen_days:
            return False, "Please select at least one active school day."
        self.school_name = school_name.strip() or "My School"
        self.school_year = school_year.strip()
        self.term_name = term_name.strip() or "Term 1"
        self.active_days = chosen_days
        self.save()
        return True, "School settings saved."

    def add_class_entry(self, day: str, name: str, textbooks: list[str], period: str = " ") -> tuple[bool, str]:
        normalized = normalize_name(name)
        if not normalized:
            return False, "Please enter a class name."
        entry = make_class_entry(normalized, [normalize_name(b) for b in textbooks if normalize_name(b)], period)
        self.timetable.setdefault(day, []).append(entry)
        self.save()
        return True, f"Added {normalized} to {day}."

# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
class TimetableApp:
    BG = "#1a1a2e"
    CARD = "#16213e"
    ACCENT = "#e94560"
    ACCENT2 = "#0f3460"
    TEXT = "#eaeaea"
    TEXT_DIM = "#a0a0b0"
    HIGHLIGHT = "#ffd369"
    GREEN = "#4ecca3"
    TAG_STYLES = {
        "day": (HIGHLIGHT, ("Helvetica Neue", 12, "bold")),
        "book": (GREEN, ("Helvetica Neue", 11)),
        "dim": (TEXT_DIM, ("Helvetica Neue", 11)),
        "class": (TEXT, ("Helvetica Neue", 11)),
    }

    def __init__(self) -> None:
        self.store = TimetableStore()
        self.store.load()
        self.subjects = list(self.store.subjects)
        self._selected_timetable_entry: tk.Entry | None = None
        self.homework_data: dict[str, list[dict]] = {day: [] for day in DAY_NAMES}
        self.serial_port: serial.Serial | None = None
        self.serial_thread: threading.Thread | None = None
        self.running = False

        self.root = tk.Tk()
        self.weight_var = tk.StringVar(master=self.root, value="0.00")
        self.root.title("School Timetable Organizer")
        self.root.geometry("780x680")
        self.root.minsize(640, 560)
        self.root.configure(bg=self.BG)

        self._style()
        self._build_main()
        self._tick_clock()
        self._center_window(self.root)

        if not self.store.textbook_list:
            self.root.after(400, self._prompt_first_setup)

    def _now(self) -> datetime:
        return datetime.now()

    def _center_window(self, win: tk.Misc) -> None:
        win.update_idletasks()
        if win is self.root:
            x = (win.winfo_screenwidth() - win.winfo_width()) // 2
            y = (win.winfo_screenheight() - win.winfo_height()) // 2
        else:
            x = self.root.winfo_x() + (self.root.winfo_width() - win.winfo_width()) // 2
            y = self.root.winfo_y() + (self.root.winfo_height() - win.winfo_height()) // 2
        win.geometry(f"+{x}+{y}")

    def _style(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=self.BG)
        style.configure("Card.TFrame", background=self.CARD)
        style.configure("TLabel", background=self.BG, foreground=self.TEXT, font=("Helvetica Neue", 11))
        style.configure("Title.TLabel", background=self.BG, foreground=self.HIGHLIGHT, font=("Helvetica Neue", 18, "bold"))
        style.configure("Clock.TLabel", background=self.CARD, foreground=self.HIGHLIGHT, font=("Helvetica Neue", 28, "bold"))
        style.configure("Sub.TLabel", background=self.CARD, foreground=self.TEXT_DIM, font=("Helvetica Neue", 11))
        style.configure("Card.TLabel", background=self.CARD, foreground=self.TEXT, font=("Helvetica Neue", 11))
        style.configure("Menu.TButton", font=("Helvetica Neue", 12), padding=(12, 10))
        style.configure("Secondary.TButton", font=("Helvetica Neue", 11), padding=(8, 6))
        style.configure("TEntry", font=("Helvetica Neue", 11), padding=4)
        style.map(
            "Menu.TButton",
            background=[("active", self.ACCENT), ("!active", self.ACCENT2)],
            foreground=[("active", "white"), ("!active", "white")],
        )

    def _tick_clock(self) -> None:
        now = self._now()
        self.clock_label.config(text=now.strftime("%I:%M %p").lstrip("0"))
        self.date_label.config(text=now.strftime("%A, %B %d, %Y"))
        self.greeting_label.config(text=f"{emoji_for_hour(now.hour)}  {greeting_for_hour(now.hour)}!")
        self._refresh_status()
        self.root.after(1000, self._tick_clock)

    def _refresh_status(self) -> None:
        year, step = setup_status(self.store.school_year, self.store.textbook_list, self.store.timetable)
        self.status_label.config(text=step)
        self.year_label.config(text=f"School year: {year}")
        self.root.title(f"{self.store.school_name} Timetable Organizer")

    def _day_choices(self) -> list[tuple[str, str]]:
        return [(d, e) for d, e in DAYS if d in self.store.active_days]

    def _prompt_first_setup(self) -> None:
        if messagebox.askyesno("Welcome", "Set up your timetable by adding classes for each school day.\n\nOpen timetable setup now?", parent=self.root):
            self.show_edit()

    def _build_main(self) -> None:
        header = ttk.Frame(self.root, style="Card.TFrame", padding=20)
        header.pack(fill="x", padx=16, pady=(16, 8))

        self.greeting_label = ttk.Label(header, text=" ", style="Card.TLabel", font=("Helvetica Neue", 14, "bold"))
        self.greeting_label.pack(anchor="w")
        self.date_label = ttk.Label(header, text=" ", style="Sub.TLabel")
        self.date_label.pack(anchor="w", pady=(4, 8))
        self.clock_label = ttk.Label(header, text=" ", style="Clock.TLabel")
        self.clock_label.pack(anchor="w")

        status_card = ttk.Frame(self.root, style="Card.TFrame", padding=12)
        status_card.pack(fill="x", padx=16, pady=4)
        self.year_label = ttk.Label(status_card, text=" ", style="Card.TLabel", foreground=self.GREEN)
        self.year_label.pack(anchor="w")
        self.status_label = ttk.Label(status_card, text=" ", style="Card.TLabel", wraplength=680)
        self.status_label.pack(anchor="w", pady=(4, 0))

        tips_frame = ttk.Frame(self.root, padding=(16, 4))
        tips_frame.pack(fill="x")
        ttk.Label(tips_frame, text="Tips", font=("Helvetica Neue", 10, "bold"), foreground=self.GREEN).pack(anchor="w")
        for tip in recommendations(self._now(), self.store.textbook_list, self.store.timetable):
            ttk.Label(tips_frame, text=f"  ·  {tip}", font=("Helvetica Neue", 10), foreground=self.TEXT_DIM).pack(anchor="w")

        ttk.Label(self.root, text=f"{self.store.school_name} Timetable Organizer", style="Title.TLabel").pack(pady=(8, 2))
        ttk.Label(self.root, text=f"{self.store.term_name} · Classes per day → daily packing", foreground=self.TEXT_DIM).pack()

        menu = ttk.Frame(self.root, padding=16)
        menu.pack(fill="both", expand=True)
        # Changed "School Textbook List" to "School Books"
        for i, (label, cmd) in enumerate([
            ("📋  School Books", self.show_textbook_list),
            ("⚖️  Schoolbag Weight Monitor", self.show_weight_monitor),
            ("📝  Homework", self.show_homework),
            ("🎒  What to Bring", self.show_packing),
            ("✏️  Edit Timetable", self.show_edit),
            ("💾  Save  & Exit", self.save_and_exit),
        ]):
            ttk.Button(menu, text=label, style="Menu.TButton", command=cmd).grid(row=i // 2, column=i % 2, sticky="nsew", padx=6, pady=6)
        for c in range(2): menu.columnconfigure(c, weight=1)
        for r in range(4): menu.rowconfigure(r, weight=1)
        self._refresh_status()

    def _popup(self, title: str, width: int = 600, height: int = 560) -> tuple[tk.Toplevel, ttk.Frame]:
        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=self.BG)
        win.geometry(f"{width}x{height}")
        win.transient(self.root)
        win.grab_set()
        frame = ttk.Frame(win, padding=16)
        frame.pack(fill="both", expand=True)
        self._center_window(win)
        return win, frame

    def _sync_subjects_with_textbooks(self) -> None:
        self.subjects = list(self.store.subjects)
        if hasattr(self, "home_subject_box"):
            vals = list(self.subjects)
            if "Miscellaneous" not in vals:
                vals.append("Miscellaneous")
            self.home_subject_box["values"] = vals
            current = self.home_subject_box.get().strip()
            if current not in vals:
                self.home_subject_box.set(vals[0] if vals else " ")

    # -----------------------------------------------------------------------
    # SCHOOL BOOKS (Previously Textbook List)
    # -----------------------------------------------------------------------
    def show_textbook_list(self) -> None:
        win, frame = self._popup("School Books", 680, 640)

        ttk.Label(frame, text="Annual book list", font=("Helvetica Neue", 14, "bold"), foreground=self.HIGHLIGHT).pack(anchor="w")
        ttk.Label(frame, text="Enter every book and set its weight in grams (g).", foreground=self.TEXT_DIM, wraplength=600).pack(anchor="w", pady=(0, 8))

        # Treeview: Book | Subject | Weight
        tree_frame = ttk.Frame(frame)
        tree_frame.pack(fill="both", expand=True, pady=8)
        tree = ttk.Treeview(tree_frame, columns=("textbook", "subject", "weight"), show="headings", selectmode="browse")
        tree.heading("textbook", text="Book Name")
        tree.heading("subject", text="Linked Subject")
        tree.heading("weight", text="Weight (g)")
        tree.column("textbook", width=240, minwidth=120, stretch=True)
        tree.column("subject", width=120, minwidth=80, stretch=False)
        tree.column("weight", width=80, minwidth=60, stretch=False)
        
        h_scroll = ttk.Scrollbar(tree_frame, orient="horizontal", command=tree.xview)
        v_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(xscrollcommand=h_scroll.set, yscrollcommand=v_scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        v_scroll.pack(side="right", fill="y")
        h_scroll.pack(side="bottom", fill="x")

        # Weight Input Controls
        weight_ctrl = ttk.Frame(frame)
        weight_ctrl.pack(fill="x", pady=6)
        ttk.Label(weight_ctrl, text="Set weight for selected (g): ", foreground=self.TEXT_DIM).pack(side="left")
        weight_entry = ttk.Entry(weight_ctrl, width=10)
        weight_entry.pack(side="left", padx=6)
        
        def refresh() -> None:
            tree.delete(*tree.get_children())
            for book in self.store.textbook_list:
                subj = self.store.textbook_subject_map.get(book, "Not linked")
                w = f"{self.store.textbook_weights.get(book, 0):.0f}"
                tree.insert("", tk.END, values=(book, subj, w))
            self._refresh_status()

        def on_select(_) -> None:
            sel = tree.selection()
            if sel:
                book = tree.item(sel[0])["values"][0]
                weight_entry.delete(0, tk.END)
                weight_entry.insert(0, f"{self.store.textbook_weights.get(book, 0):.0f}")
            else:
                weight_entry.delete(0, tk.END)
                
        tree.bind("<<TreeviewSelect>>", on_select)

        def update_weight() -> None:
            sel = tree.selection()
            if not sel:
                messagebox.showwarning("Weight", "Select a book first.", parent=win)
                return
            try:
                w = int(float(weight_entry.get()))
                book = tree.item(sel[0])["values"][0]
                self.store.set_textbook_weight(book, w)
                refresh()
            except ValueError:
                messagebox.showwarning("Weight", "Enter a valid number.", parent=win)

        ttk.Button(weight_ctrl, text="Set", command=update_weight).pack(side="left", padx=4)

        # Add Book Controls
        add_row = ttk.Frame(frame)
        add_row.pack(fill="x", pady=6)
        ttk.Label(add_row, text="Add book: ", foreground=self.TEXT_DIM).pack(side="left")
        single_entry = ttk.Entry(add_row)
        single_entry.pack(side="left", fill="x", expand=True, padx=6)
        single_entry.bind("<Return>", lambda _: add_single())

        link_row = ttk.Frame(frame)
        link_row.pack(fill="x", pady=(8, 6))
        ttk.Label(link_row, text="Link to subject: ", foreground=self.TEXT_DIM).pack(side="left")
        subject_link_var = tk.StringVar(value=self.subjects[0] if self.subjects else " ")
        subject_link_box = ttk.Combobox(link_row, textvariable=subject_link_var, values=self.subjects, state="readonly", width=18)
        subject_link_box.pack(side="left", padx=6, fill="x", expand=False)

        def add_subject_inline() -> None:
            val = simpledialog.askstring("Add Subject", "Enter subject name:", parent=win)
            if not val: return
            val = normalize_name(val)
            if not val: return
            if self.store.add_subject_name(val):
                self.subjects = list(self.store.subjects)
                subject_link_box["values"] = self.subjects
                subject_link_var.set(val)
                self._sync_subjects_with_textbooks()
                messagebox.showinfo("Added", f"Subject '{val}' added.", parent=win)
            else:
                messagebox.showwarning("Add", "Subject already exists.", parent=win)

        def remove_subject_inline() -> None:
            subj = subject_link_var.get().strip()
            if not subj: return
            if messagebox.askyesno("Remove Subject", f"Remove '{subj}'?", parent=win):
                self.store.remove_subject_name(subj)
                self.subjects = list(self.store.subjects)
                subject_link_box["values"] = self.subjects
                if self.subjects:
                    subject_link_var.set(self.subjects[0])
                self._sync_subjects_with_textbooks()
                refresh()
                messagebox.showinfo("Removed", f"Subject '{subj}' removed.", parent=win)

        ttk.Button(link_row, text="＋", command=add_subject_inline, width=2).pack(side="left", padx=2)
        ttk.Button(link_row, text="－", command=remove_subject_inline, width=2).pack(side="left", padx=2)

        def add_single() -> None:
            name = normalize_name(single_entry.get())
            subj = subject_link_var.get().strip()
            if not name:
                messagebox.showwarning("Add", "Enter a book name.", parent=win)
                return
            if not subj:
                messagebox.showwarning("Add", "Select a subject first.", parent=win)
                return
            self.store.add_textbook_names([name])
            self.store.link_textbook_to_subject(name, subj)
            single_entry.delete(0, tk.END)
            refresh()
            messagebox.showinfo("Added", f"Added '{name}' linked to '{subj}'.", parent=win)

        def link_selected() -> None:
            sel = tree.selection()
            if not sel:
                messagebox.showwarning("Link", "Select a book.", parent=win)
                return
            self.store.link_textbook_to_subject(tree.item(sel[0])["values"][0], subject_link_var.get().strip())
            refresh()

        ttk.Button(link_row, text="Link", command=link_selected).pack(side="left", padx=(6, 0))

        paste_box = tk.Text(frame, height=3, bg=self.CARD, fg=self.TEXT, relief="flat", wrap="word")
        paste_box.pack(fill="x", pady=4)
        ttk.Label(frame, text="Or paste multiple (one per line). All use selected subject:", foreground=self.TEXT_DIM).pack(anchor="w")

        def add_pasted() -> None:
            subj = subject_link_var.get().strip()
            if not subj: messagebox.showwarning("Add", "Select a subject first.", parent=win); return
            lines = [l.strip() for l in paste_box.get("1.0", tk.END).splitlines() if normalize_name(l)]
            if not lines: messagebox.showwarning("Add", "No valid names.", parent=win); return
            added, skipped = self.store.add_textbook_names(lines)
            for name in lines:
                norm = normalize_name(name)
                if norm in self.store.textbook_list: self.store.link_textbook_to_subject(norm, subj)
            paste_box.delete("1.0", tk.END)
            refresh()
            messagebox.showinfo("Import", f"Added {added}, skipped {skipped}. Linked to '{subj}'.", parent=win)

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill="x", pady=8)
        def remove_selected() -> None:
            sel = tree.selection()
            if not sel: messagebox.showwarning("Remove", "Select a book.", parent=win); return
            book = tree.item(sel[0])["values"][0]
            if messagebox.askyesno("Remove", f"Remove '{book}'?", parent=win):
                self.store.remove_textbook(book)
                refresh()
        ttk.Button(btn_row, text="Remove Selected", command=remove_selected).pack(side="left")
        ttk.Button(btn_row, text="Add Pasted", command=add_pasted).pack(side="left", padx=8)
        ttk.Button(frame, text="Done", command=win.destroy).pack(pady=(4, 0))
        refresh()

    # -----------------------------------------------------------------------
    # WHAT TO BRING (Updated)
    # -----------------------------------------------------------------------
    def show_packing(self) -> None:
        available = days_with_classes(self.store.timetable)
        if not available:
            messagebox.showinfo("What to Bring", "Add classes to your timetable first.", parent=self.root)
            return

        win, frame = self._popup("What to Bring", 540, 560)
        next_school_date = self.get_next_school_day()
        next_day_name = next_school_date.strftime("%A")
        next_date_str = next_school_date.strftime("%d/%m/%Y")
        
        ttk.Label(frame, text=f"Next School Day: {next_day_name}", font=("Helvetica Neue", 14, "bold"), foreground=self.HIGHLIGHT).pack(anchor="w", pady=(0, 4))
        ttk.Label(frame, text=f"Date: {next_date_str} (Auto-calculated)", foreground=self.TEXT_DIM).pack(anchor="w", pady=(0, 8))

        result = tk.Text(frame, font=("Helvetica Neue", 11), bg=self.CARD, fg=self.TEXT, relief="flat", height=14, wrap="word")
        result.pack(fill="both", expand=True)

        def update_list() -> None:
            # Get textbooks for the day
            class_rows, all_books = consolidate_classes_for_day(self.store.timetable, next_day_name)
            
            # Get homework due on this specific date
            homework_due = [t for day_tasks in self.homework_data.values() for t in day_tasks if t.get("due_date") == next_date_str]
            
            # Add homework-linked books to the total books list for weight calculation
            for item in homework_due:
                linked_book = item.get("linked_book")
                if linked_book and linked_book not in all_books:
                    all_books.append(linked_book)

            result.config(state="normal")
            result.delete("1.0", tk.END)
            result.insert(tk.END, format_packing_text(next_day_name, class_rows, all_books, homework_due, self.store.textbook_weights))
            result.config(state="disabled")

        update_list()
        ttk.Button(frame, text="Close", command=win.destroy).pack(pady=8)

    def get_next_school_day(self) -> datetime:
        check = self._now() + timedelta(days=1)
        for _ in range(7):
            if check.strftime("%A") in self.store.active_days: return check
            check += timedelta(days=1)
        return self._now() + timedelta(days=1)

    def calculate_expected_weight(self) -> float:
        """Calculate the expected schoolbag weight for the next school day."""
        try:
            next_school_date = self.get_next_school_day()
            next_day_name = next_school_date.strftime("%A")
            next_date_str = next_school_date.strftime("%d/%m/%Y")
            
            _, all_books = consolidate_classes_for_day(self.store.timetable, next_day_name)
            homework_due = [t for day_tasks in self.homework_data.values() for t in day_tasks if t.get("due_date") == next_date_str]
            for item in homework_due:
                linked_book = item.get("linked_book")
                if linked_book and linked_book not in all_books:
                    all_books.append(linked_book)
                    
            book_weight = sum(self.store.textbook_weights.get(b, 0) for b in all_books)
            return book_weight + 1000
        except Exception:
            return 1000.0

    # -----------------------------------------------------------------------
    # HOMEWORK (Updated with Book Linking)
    # -----------------------------------------------------------------------
    def show_homework(self) -> None:
        win, frame = self._popup("Homework Planner", 720, 560)
        ttk.Label(frame, text="Schedule homework with subjects and dates.", foreground=self.TEXT_DIM, wraplength=640).pack(anchor="w", pady=(0, 10))

        control_card = ttk.Frame(frame, style="Card.TFrame", padding=12)
        control_card.pack(fill="x")
        control_card.columnconfigure(5, weight=1)

        ttk.Label(control_card, text="Day: ", style="Card.TLabel").grid(column=0, row=0, sticky="w")
        self.day_var = tk.StringVar(value=datetime.now().strftime("%A"))
        self.day_label = ttk.Label(control_card, textvariable=self.day_var, style="Card.TLabel", width=12)
        self.day_label.grid(column=1, row=0, sticky="w", padx=(6, 14))

        ttk.Label(control_card, text="Date: ", style="Card.TLabel").grid(column=2, row=0, sticky="w")
        self.date_entry = ttk.Entry(control_card, width=12)
        self.date_entry.grid(column=3, row=0, sticky="w", padx=(6, 4))
        self.date_entry.insert(0, datetime.now().strftime("%d/%m/%Y"))
        self.date_entry.bind("<FocusOut>", self._update_home_day_from_date)
        self.date_entry.bind("<Return>", self._update_home_day_from_date)
        ttk.Button(control_card, text="📅 ", width=3, command=self.open_homework_calendar).grid(column=4, row=0, sticky="w")

        ttk.Label(control_card, text="Subject: ", style="Card.TLabel").grid(column=5, row=0, sticky="w", padx=(10, 0))
        home_subs = list(self.subjects)
        if "Miscellaneous" not in home_subs: home_subs.append("Miscellaneous")
        self.home_subject_box = ttk.Combobox(control_card, values=home_subs, state="readonly", width=18)
        self.home_subject_box.grid(column=6, row=0, sticky="w")
        if home_subs: self.home_subject_box.set(home_subs[0])

        ttk.Label(control_card, text="Task: ", style="Card.TLabel").grid(column=0, row=1, sticky="w", pady=(12, 0))
        self.homework_entry = ttk.Entry(control_card)
        self.homework_entry.grid(column=1, row=1, columnspan=5, sticky="we", pady=(12, 0))
        
        ttk.Label(control_card, text="Due: ", style="Card.TLabel").grid(column=0, row=2, sticky="w", pady=(4, 0))
        self.due_date_entry = ttk.Entry(control_card, width=12)
        self.due_date_entry.grid(column=1, row=2, sticky="w", padx=(6, 4))
        self.due_date_entry.insert(0, datetime.now().strftime("%d/%m/%Y"))
        ttk.Button(control_card, text="📅 ", width=3, command=lambda: self.open_homework_calendar(is_due=True)).grid(column=2, row=2, sticky="w")
        
        # New: Link to Book Dropdown
        ttk.Label(control_card, text="Link Book: ", style="Card.TLabel").grid(column=0, row=3, sticky="w", pady=(4, 0))
        self.homework_book_box = ttk.Combobox(control_card, values=[""] + self.store.textbook_list, state="readonly", width=18)
        self.homework_book_box.grid(column=1, row=3, sticky="w", padx=(6, 4))
        self.homework_book_box.set("") # Default to empty
        
        ttk.Button(control_card, text="Add Task", command=self.add_homework).grid(column=6, row=1, rowspan=3, padx=(10, 0), pady=(12, 0), sticky="ns")

        list_frame = ttk.Frame(frame, style="Card.TFrame", padding=12)
        list_frame.pack(fill="both", expand=True, pady=(16, 0))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self.homework_tree = ttk.Treeview(list_frame, columns=("task", "due"), show="headings", selectmode="browse")
        self.homework_tree.heading("task", text="Homework Task")
        self.homework_tree.heading("due", text="Due Date")
        self.homework_tree.column("task", width=400, minwidth=200, stretch=True)
        self.homework_tree.column("due", width=100, minwidth=80, stretch=False)
        
        h_scroll = ttk.Scrollbar(list_frame, orient="horizontal", command=self.homework_tree.xview)
        v_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.homework_tree.yview)
        self.homework_tree.configure(xscrollcommand=h_scroll.set, yscrollcommand=v_scroll.set)
        
        self.homework_tree.grid(column=0, row=0, sticky="nsew")
        v_scroll.grid(column=1, row=0, sticky="ns")
        h_scroll.grid(column=0, row=1, sticky="ew")

        footer = ttk.Frame(frame, style="Card.TFrame", padding=8)
        footer.pack(fill="x", pady=(12, 0))
        ttk.Button(footer, text="Remove Selected", command=self.remove_homework).pack(side="left")
        ttk.Button(footer, text="Clear Day", command=self.clear_homework_day).pack(side="left", padx=10)
        ttk.Button(footer, text="Export Homework", command=self.export_homework).pack(side="right")

        self.update_homework_list()
        ttk.Button(frame, text="Close", command=win.destroy).pack(pady=8)

    def show_edit(self) -> None:
        win, frame = self._popup("Edit Timetable", 980, 680)
        day_choices = self._day_choices()
        if not day_choices:
            messagebox.showwarning("No school days", "No active school days available.", parent=win)
            win.destroy()
            return

        ttk.Label(frame, text="Edit classes in the timetable grid. Click a cell to choose from subjects.", foreground=self.TEXT_DIM, wraplength=880).pack(anchor="w", pady=(0, 8))

        top_controls = ttk.Frame(frame, style="Card.TFrame", padding=12)
        top_controls.pack(fill="x", pady=(0, 8))
        week_row = ttk.Frame(top_controls)
        week_row.pack(fill="x")

        ttk.Label(week_row, text="Week: ", style="Sub.TLabel").grid(column=0, row=0, sticky="w")
        week_names = sorted(self.store.timetable_weeks.keys() or ["Week A"])
        self._week_box_var = tk.StringVar(value=self.store.active_week)
        week_box = ttk.Combobox(week_row, values=week_names, textvariable=self._week_box_var, state="readonly", width=14)
        week_box.grid(column=1, row=0, sticky="w", padx=(8, 0))

        def commit_grid_to_store() -> None:
            nonlocal current_period_count
            new_timetable: dict[str, list[dict]] = {}
            for day in days:
                rows: list[dict] = []
                for idx, entry in enumerate(entries[day], start=1):
                    name = normalize_name(entry.get())
                    if not name: continue
                    rows.append(make_class_entry(name, self.store.textbooks_for_subject(name), f"Period {idx}"))
                if rows: new_timetable[day] = rows
            self.store.timetable = new_timetable
            self.store.period_counts[self.store.active_week] = current_period_count
            self.store.save()
            self._refresh_status()

        def ensure_week_exists() -> None:
            if not self.store.timetable_weeks:
                self.store.timetable_weeks = {"Week A": {}}
                self.store.active_week = "Week A"
            if self.store.active_week not in self.store.timetable_weeks:
                self.store.active_week = next(iter(self.store.timetable_weeks.keys()))
            if self.store.active_week not in self.store.period_counts:
                self.store.period_counts[self.store.active_week] = infer_period_count(self.store.timetable, default=6)

        ensure_week_exists()
        period_row = ttk.Frame(top_controls)
        period_row.pack(fill="x", pady=(10, 0))

        ttk.Label(period_row, text="Periods: ", style="Sub.TLabel").grid(column=0, row=0, sticky="w")
        periods_value_lbl = ttk.Label(period_row, text=" ", style="Sub.TLabel")
        periods_value_lbl.grid(column=1, row=0, sticky="w", padx=(8, 0))

        days = [d for d, _ in day_choices]
        current_period_count = int(self.store.period_counts.get(self.store.active_week, 6) or 6)
        if current_period_count < 1: current_period_count = 1
        self.store.period_counts[self.store.active_week] = current_period_count
        periods_value_lbl.config(text=str(current_period_count))

        entries: dict[str, list[tk.Entry]] = {}
        table_outer = ttk.Frame(frame, style="Card.TFrame", padding=8)
        table_outer.pack(fill="both", expand=True, pady=(0, 8))
        table_outer.columnconfigure(0, weight=1)
        table_outer.rowconfigure(0, weight=1)

        canvas = tk.Canvas(table_outer, bg=self.CARD, highlightthickness=0)
        h_scroll = ttk.Scrollbar(table_outer, orient="horizontal", command=canvas.xview)
        v_scroll = ttk.Scrollbar(table_outer, orient="vertical", command=canvas.yview)
        canvas.configure(xscrollcommand=h_scroll.set, yscrollcommand=v_scroll.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        h_scroll.grid(row=1, column=0, sticky="ew")
        v_scroll.grid(row=0, column=1, sticky="ns")

        table_inner = ttk.Frame(canvas, style="Card.TFrame")
        table_inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=table_inner, anchor="nw")

        def clear_grid() -> None:
            for child in list(table_inner.winfo_children()):
                child.destroy()

        def row_values_for_day(day: str) -> list[str]:
            classes = list(self.store.timetable.get(day, []))
            row = [" "] * current_period_count
            for index, item in enumerate(classes):
                name = item.get("name", " ").strip()
                if not name: continue
                period_label = item.get("period", " ").strip()
                num = parse_period_number(period_label) if period_label else None
                if num is not None and 1 <= num <= current_period_count:
                    row[num - 1] = name
                elif index < current_period_count and not row[index]:
                    row[index] = name
            return row

        def build_grid() -> None:
            clear_grid()
            periods = [f"Period {i}" for i in range(1, current_period_count + 1)]
            entries.clear()

            ttk.Label(table_inner, text="Day / Period", anchor="center", background="#ffffff", foreground="#111827").grid(column=0, row=0, padx=4, pady=4, sticky="nsew")
            for col, period in enumerate(periods, start=1):
                ttk.Label(table_inner, text=period, anchor="center", background="#ffffff", foreground="#111827").grid(column=col, row=0, padx=4, pady=4, sticky="nsew")

            for row_idx, day in enumerate(days, start=1):
                ttk.Label(table_inner, text=f"{DAY_EMOJI.get(day, '')} {day}", anchor="w", background="#ffffff", foreground="#111827").grid(column=0, row=row_idx, padx=4, pady=4, sticky="nsew")
                entries[day] = []
                row_values = row_values_for_day(day)
                for col in range(current_period_count):
                    entry = tk.Entry(table_inner, width=18, font=("Helvetica Neue", 10), fg="#111827", bg="#ffffff", insertbackground="#111827", relief="flat")
                    entry.grid(column=col + 1, row=row_idx, padx=4, pady=4, sticky="nsew")
                    entry.insert(0, row_values[col])
                    entry.bind("<Button-1>", self._show_subjects_menu)
                    entries[day].append(entry)
            for i in range(current_period_count + 1):
                table_inner.columnconfigure(i, weight=1)

        def next_week_name() -> str:
            existing = [name for name in (self.store.timetable_weeks.keys() or []) if name.startswith("Week ") and len(name) == 6]
            letters = {name[-1] for name in existing}
            for code in range(ord("A"), ord("Z") + 1):
                candidate = f"Week {chr(code)}"
                if candidate[-1] not in letters:
                    return candidate
            return f"Week {len(existing) + 1}"

        def refresh_week_box() -> None:
            names = sorted(self.store.timetable_weeks.keys()) if self.store.timetable_weeks else ["Week A"]
            week_box["values"] = names
            if self.store.active_week in names:
                self._week_box_var.set(self.store.active_week)
            else:
                self.store.active_week = names[0]
                self._week_box_var.set(names[0])

        def add_week() -> None:
            nonlocal current_period_count
            commit_grid_to_store()
            new_week = next_week_name()
            self.store.timetable_weeks[new_week] = {}
            self.store.active_week = new_week
            self.store.period_counts[new_week] = current_period_count
            self.store.timetable = self.store.timetable_weeks[new_week]
            refresh_week_box()
            current_period_count = int(self.store.period_counts[new_week])
            periods_value_lbl.config(text=str(current_period_count))
            build_grid()

        def remove_week() -> None:
            if len(self.store.timetable_weeks) <= 1:
                messagebox.showwarning("Remove Week", "Cannot remove the last remaining week.", parent=win)
                return
            if not messagebox.askyesno("Remove Week", f"Remove '{self.store.active_week}'? This cannot be undone.", parent=win):
                return
            commit_grid_to_store()
            removed = self.store.active_week
            del self.store.timetable_weeks[removed]
            remaining = sorted(self.store.timetable_weeks.keys())
            self.store.active_week = remaining[0] if remaining else "Week A"
            self.store.timetable = self.store.timetable_weeks.get(self.store.active_week, {})
            if self.store.active_week not in self.store.period_counts:
                self.store.period_counts[self.store.active_week] = infer_period_count(self.store.timetable, default=6)
            nonlocal current_period_count
            current_period_count = int(self.store.period_counts.get(self.store.active_week, 6))
            periods_value_lbl.config(text=str(current_period_count))
            self.store.save()
            refresh_week_box()
            build_grid()

        def add_period() -> None:
            nonlocal current_period_count
            commit_grid_to_store()
            current_period_count += 1
            self.store.period_counts[self.store.active_week] = current_period_count
            periods_value_lbl.config(text=str(current_period_count))
            self.store.save()
            build_grid()

        def remove_period() -> None:
            nonlocal current_period_count
            if current_period_count <= 1:
                messagebox.showwarning("Remove Period", "Cannot remove the last period.", parent=win)
                return
            commit_grid_to_store()
            current_period_count -= 1
            self.store.period_counts[self.store.active_week] = current_period_count
            periods_value_lbl.config(text=str(current_period_count))
            self.store.save()
            build_grid()

        ttk.Button(week_row, text="Add Week", style="Secondary.TButton", width=11, command=add_week).grid(column=2, row=0, padx=(10, 6))
        ttk.Button(week_row, text="Remove Week", style="Secondary.TButton", width=11, command=remove_week).grid(column=3, row=0, padx=(0, 0))
        ttk.Button(period_row, text="Add Period", style="Secondary.TButton", width=11, command=add_period).grid(column=2, row=0, padx=(10, 6))
        ttk.Button(period_row, text="Remove Period", style="Secondary.TButton", width=11, command=remove_period).grid(column=3, row=0, padx=(0, 0))

        def on_week_selected(*_) -> None:
            selected = week_box.get().strip()
            if not selected or selected == self.store.active_week: return
            commit_grid_to_store()
            self.store.active_week = selected
            self.store.timetable = self.store.timetable_weeks.get(selected, {})
            nonlocal current_period_count
            current_period_count = int(self.store.period_counts.get(selected, 6) or 6)
            current_period_count = max(1, current_period_count)
            self.store.period_counts[selected] = current_period_count
            periods_value_lbl.config(text=str(current_period_count))
            self.store.save()
            build_grid()

        week_box.bind("<<ComboboxSelected>>", on_week_selected)
        refresh_week_box()
        build_grid()

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill="x", pady=8)
        def save_and_close() -> None:
            commit_grid_to_store()
            messagebox.showinfo("Saved", "Timetable saved.", parent=win)
            win.destroy()
        ttk.Button(btn_row, text="Save", command=save_and_close).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="Cancel", command=win.destroy).pack(side="left")

    def add_subject(self) -> None:
        val = simpledialog.askstring("New Subject", "Enter subject name:", parent=self.root)
        if not val: return
        val = normalize_name(val)
        if not val: return
        added = self.store.add_subject_name(val)
        self._sync_subjects_with_textbooks()
        if added: return
        messagebox.showwarning("New Subject", "Subject already exists.", parent=self.root)

    def remove_subject(self) -> None:
        entry = self._selected_timetable_entry
        if entry is None: return
        current = normalize_name(entry.get())
        if not current or current not in self.subjects:
            self._selected_timetable_entry = None
            return
        if messagebox.askyesno("Remove Subject", f"Remove '{current}' from the subject list?", parent=self.root):
            self.store.remove_subject_name(current)
            self._sync_subjects_with_textbooks()
        self._selected_timetable_entry = None

    def _show_subjects_menu(self, event: tk.Event) -> None:
        entry = event.widget
        if not isinstance(entry, tk.Entry): return
        self._selected_timetable_entry = entry
        menu = tk.Menu(self.root, tearoff=False)
        for subj in self.subjects:
            menu.add_command(label=subj, command=lambda s=subj: (entry.delete(0, tk.END), entry.insert(0, s)))
        menu.add_separator()
        menu.add_command(label="Add Subject", command=self.add_subject)
        menu.add_command(label="Remove Subject", command=self.remove_subject)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.after(200, menu.destroy)

    def open_homework_calendar(self, is_due: bool = False) -> None:
        entry_widget = self.due_date_entry if is_due else self.date_entry
        try: current_date = datetime.strptime(entry_widget.get(), "%d/%m/%Y").date()
        except Exception: current_date = datetime.now().date()
        year, month = current_date.year, current_date.month
        cal_win = tk.Toplevel(self.root)
        cal_win.title("Select Date")
        cal_win.configure(bg=self.BG)
        cal_win.transient(self.root)
        cal_win.grab_set()
        header = ttk.Frame(cal_win, padding=8)
        header.pack(fill="x")
        month_var = tk.StringVar(value=current_date.strftime("%B %Y"))
        body = ttk.Frame(cal_win, padding=8)
        body.pack()
        def refresh_calendar() -> None:
            month_var.set(datetime(year, month, 1).strftime("%B %Y"))
            for widget in body.winfo_children(): widget.destroy()
            weekdays = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"]
            for col, name in enumerate(weekdays):
                ttk.Label(body, text=name, width=4, anchor="center").grid(row=0, column=col, padx=2, pady=2)
            month_days = calendar.Calendar(firstweekday=6).monthdayscalendar(year, month)
            for row_index, week in enumerate(month_days, start=1):
                for col_index, day in enumerate(week):
                    if day == 0:
                        ttk.Label(body, text=" ", width=4).grid(row=row_index, column=col_index, padx=2, pady=2)
                        continue
                    btn = ttk.Button(body, text=str(day), width=4, command=lambda d=day: select_date(d))
                    btn.grid(row=row_index, column=col_index, padx=1, pady=1)
        def select_date(day: int) -> None:
            selected = datetime(year, month, day).date()
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, selected.strftime("%d/%m/%Y"))
            if not is_due:
                self._update_home_day_from_date()
            cal_win.destroy()
        def change_month(delta: int) -> None:
            nonlocal year, month
            month += delta
            if month < 1: month, year = 12, year - 1
            elif month > 12: month, year = 1, year + 1
            refresh_calendar()
        ttk.Button(header, text=" < ", width=3, command=lambda: change_month(-1)).pack(side="left")
        ttk.Label(header, textvariable=month_var, foreground=self.TEXT).pack(side="left", expand=True)
        ttk.Button(header, text=" > ", width=3, command=lambda: change_month(1)).pack(side="right")
        refresh_calendar()

    def _update_home_day_from_date(self, event: tk.Event | None = None) -> None:
        date_text = self.date_entry.get().strip()
        try:
            selected = datetime.strptime(date_text, "%d/%m/%Y").date()
            self.day_var.set(selected.strftime("%A"))
        except Exception: pass
        self.update_homework_list()

    def update_homework_list(self) -> None:
        day = self.day_var.get() or "Monday"
        tasks = self.homework_data.get(day, [])
        for item in self.homework_tree.get_children():
            self.homework_tree.delete(item)
        for item in tasks:
            subject = item.get("subject", "Miscellaneous")
            task = item.get("task", " ")
            due = item.get("due_date", "N/A")
            display_text = f"{subject}: {task}"
            self.homework_tree.insert("", tk.END, values=(display_text, due))

    def add_homework(self) -> None:
        self._update_home_day_from_date()
        day = self.day_var.get() or "Monday"
        date_text = self.date_entry.get().strip()
        subject = (self.home_subject_box.get() or " ").strip() or "Miscellaneous"
        task = self.homework_entry.get().strip()
        due_date_text = self.due_date_entry.get().strip()
        linked_book = self.homework_book_box.get().strip()
        
        if not task:
            messagebox.showwarning("Add Task", "Enter a homework task.", parent=self.root)
            return
        if not date_text:
            messagebox.showwarning("Add Task", "Enter a date.", parent=self.root)
            return
        if not due_date_text:
            messagebox.showwarning("Add Task", "Enter a due date.", parent=self.root)
            return
            
        try: 
            datetime.strptime(date_text, "%d/%m/%Y")
            datetime.strptime(due_date_text, "%d/%m/%Y")
        except ValueError:
            messagebox.showwarning("Add Task", "Valid dates (DD/MM/YYYY) required.", parent=self.root)
            return
            
        self.homework_data.setdefault(day, []).append({
            "date": date_text, 
            "subject": subject, 
            "task": task,
            "due_date": due_date_text,
            "linked_book": linked_book if linked_book else None
        })
        self.homework_entry.delete(0, tk.END)
        self.update_homework_list()

    def remove_homework(self) -> None:
        day = self.day_var.get() or "Monday"
        selected = self.homework_tree.selection()
        if not selected:
            messagebox.showwarning("Remove Task", "Select a task.", parent=self.root)
            return
        all_items = self.homework_tree.get_children()
        index = all_items.index(selected[0])
        if day in self.homework_data and 0 <= index < len(self.homework_data[day]):
            self.homework_data[day].pop(index)
        self.update_homework_list()

    def clear_homework_day(self) -> None:
        day = self.day_var.get() or "Monday"
        if messagebox.askyesno("Clear Tasks", f"Clear all homework for {day}?", parent=self.root):
            self.homework_data[day] = []
            self.update_homework_list()

    def export_homework(self) -> None:
        path = filedialog.asksaveasfilename(title="Export Homework", defaultextension=".txt", filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path: return
        try:
            with open(path, "w", encoding="utf-8") as file:
                for day, tasks in self.homework_data.items():
                    file.write(f"{day}:\n")
                    for item in tasks:
                        subject = item.get("subject", "Miscellaneous")
                        task = item.get("task", " ")
                        due = item.get("due_date", "N/A")
                        file.write(f"  - {subject} | {task} | Due: {due}\n")
                    file.write("\n")
            messagebox.showinfo("Exported", f"Homework exported to {path}.", parent=self.root)
        except Exception as exc:
            messagebox.showerror("Export Failed", f"Unable to export homework: {exc}", parent=self.root)

    def show_weight_monitor(self) -> None:
        win, frame = self._popup("Schoolbag Weight Monitor", 680, 460)
        ttk.Label(frame, text="Connect your load cell and monitor live schoolbag weight.", foreground=self.TEXT_DIM, wraplength=620).pack(anchor="w", pady=(0, 10))
        control_card = ttk.Frame(frame, style="Card.TFrame", padding=12)
        control_card.pack(fill="x", pady=(0, 10))
        control_card.columnconfigure(1, weight=1)
        ttk.Label(control_card, text="Serial Port: ", style="Card.TLabel").grid(column=0, row=0, sticky="w")
        self.port_box = ttk.Combobox(control_card, values=[], state="readonly", width=26)
        self.port_box.grid(column=1, row=0, sticky="w", padx=(8, 6))
        ttk.Button(control_card, text="Refresh", command=self.update_ports).grid(column=2, row=0, sticky="w")
        self.connect_button = ttk.Button(control_card, text="Connect", command=self.toggle_connection)
        self.connect_button.grid(column=0, row=1, pady=(12, 0), sticky="w")
        self.tare_button = ttk.Button(control_card, text="Tare", command=self.send_tare, state=tk.DISABLED)
        self.tare_button.grid(column=1, row=1, pady=(12, 0), sticky="w")
        self.status_label = ttk.Label(control_card, text="Status: Disconnected", style="Sub.TLabel")
        self.status_label.grid(column=0, row=2, columnspan=3, sticky="w", pady=(10, 0))
        weight_card = ttk.Frame(frame, style="Card.TFrame", padding=18)
        weight_card.pack(fill="x")
        ttk.Label(weight_card, text="Current Weight", style="Card.TLabel").pack(anchor="w")
        ttk.Label(weight_card, textvariable=self.weight_var, font=("Helvetica Neue", 36, "bold"), foreground=self.HIGHLIGHT).pack(anchor="w", pady=(10, 0))
        ttk.Label(weight_card, text="g", style="Sub.TLabel").pack(anchor="w")
        
        # Display Expected Weight for comparison
        expected_total = self.calculate_expected_weight()
        next_school_date = self.get_next_school_day()
        next_day_name = next_school_date.strftime("%A")
        
        ttk.Label(weight_card, text=f"Expected for {next_day_name}: {expected_total:.0f} g", style="Sub.TLabel", foreground=self.GREEN).pack(anchor="w", pady=(5, 0))

        def close_monitor() -> None:
            if self.running: self.stop_serial()
            win.destroy()
        ttk.Button(frame, text="Close", command=close_monitor).pack(pady=12)
        win.protocol("WM_DELETE_WINDOW", close_monitor)
        self.update_ports()

    def update_ports(self) -> None:
        ports = [port.device for port in serial.tools.list_ports.comports()]
        if hasattr(self, "port_box"):
            self.port_box["values"] = ports
            if ports:
                current = self.port_box.get().strip()
                if current not in ports: self.port_box.current(0)
            else: self.port_box.set(" ")

    def toggle_connection(self) -> None:
        if self.running: self.stop_serial()
        else: self.start_serial()

    def start_serial(self) -> None:
        if not hasattr(self, "port_box"): return
        port = self.port_box.get().strip()
        if not port:
            messagebox.showwarning("Select Port", "Please select a serial port first.", parent=self.root)
            return
        try: self.serial_port = serial.Serial(port, 9600, timeout=1)
        except serial.SerialException as exc:
            messagebox.showerror("Serial Error", f"Failed to open port: {exc}", parent=self.root)
            return
        self.running = True
        if hasattr(self, "connect_button"): self.connect_button.config(text="Disconnect")
        if hasattr(self, "tare_button"): self.tare_button.config(state=tk.NORMAL)
        if hasattr(self, "status_label"): self.status_label.config(text=f"Status: Connected to {port}")
        self.serial_thread = threading.Thread(target=self.read_loop, daemon=True)
        self.serial_thread.start()

    def stop_serial(self) -> None:
        self.running = False
        if hasattr(self, "connect_button"): self.connect_button.config(text="Connect")
        if hasattr(self, "tare_button"): self.tare_button.config(state=tk.DISABLED)
        if hasattr(self, "status_label"): self.status_label.config(text="Status: Disconnected")
        if self.serial_port and self.serial_port.is_open: self.serial_port.close()
        self.serial_port = None

    def read_loop(self) -> None:
        expected_total = self.calculate_expected_weight()
        last_warning_state = "ok"  # Tracks: "ok", "light", "heavy"

        while self.running and self.serial_port and self.serial_port.is_open:
            try:
                line = self.serial_port.readline().decode("utf-8", errors="ignore").strip()
                if line.startswith("WEIGHT:"):
                    try:
                        raw_val = line.split(": ", 1)[1].strip()
                        current_weight = float(raw_val)
                        
                        # Update UI label safely on main thread
                        self.root.after(0, lambda v=f"{current_weight:.0f}": self.weight_var.set(v))
                        
                        diff = current_weight - expected_total
                        new_state = "ok"
                        if diff < -1000:
                            new_state = "light"
                        elif diff > 1000:
                            new_state = "heavy"
                        
                        # Only trigger warning if state actually changes (prevents UI spam/freeze)
                        if new_state != last_warning_state:
                            last_warning_state = new_state
                            if new_state == "light":
                                msg = "Did you forget to pack something? Check the list again!"
                            elif new_state == "heavy":
                                msg = "Your bag is heavier than expected. Did you pack something extra?"
                            else:
                                msg = None
                            
                            if msg:
                                self.root.after(0, lambda m=msg: messagebox.showwarning("Weight Alert", m, parent=self.root))
                                
                    except ValueError:
                        pass
                elif line == "TARE_COMPLETE":
                    self.root.after(0, lambda: messagebox.showinfo("Info", "Tare completed", parent=self.root))
                elif line == "HX711_NOT_READY":
                    self.root.after(0, lambda: self.weight_var.set("---"))
            except serial.SerialException:
                break
            except Exception:
                continue
                
        self.running = False
        self.root.after(0, self.stop_serial)

    def send_tare(self) -> None:
        if self.serial_port and self.serial_port.is_open:
            try: self.serial_port.write(b"T")
            except serial.SerialException as exc:
                messagebox.showerror("Serial Error", f"Failed to send tare: {exc}", parent=self.root)

    def save_and_exit(self) -> None:
        if self.running: self.stop_serial()
        self.store.save()
        messagebox.showinfo("Goodbye", f"Saved for {self.store.school_name}. Have a great day at school!", parent=self.root)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()

if __name__ == "__main__":
    TimetableApp().run()