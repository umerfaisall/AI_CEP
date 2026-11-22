import copy
import heapq
import re
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


# Matches a coordinate such as "(3,7)" or "(3, 7)" and captures the row and column numbers.
COORDINATE_PATTERN = re.compile(r'\(\s*(\d+)\s*,\s*(\d+)\s*\)')


def _read_coordinates(file_lines, line_number, how_many):
    """Read `how_many` (row,col) tuples starting at file_lines[line_number].
    The tuples may be spread over several lines. Returns (coordinates, next_line_number)."""
    coordinates = []
    while len(coordinates) < how_many:
        if line_number >= len(file_lines):
            raise ValueError(f"Expected {how_many} coordinates but found only {len(coordinates)}.")
        for row_text, col_text in COORDINATE_PATTERN.findall(file_lines[line_number]):
            coordinates.append((int(row_text), int(col_text)))
        line_number += 1
    if len(coordinates) != how_many:
        raise ValueError(f"Expected {how_many} coordinates but found {len(coordinates)}.")
    return coordinates, line_number


def parse_environment(file_path):
    """Load Environment.txt -> (grid, charging_station, dirty_locations). Raises ValueError if malformed."""
    with open(file_path, 'r', encoding='utf-8') as env_file:
        file_lines = [text.strip() for text in env_file if text.strip()]   # ignore blank lines
    if len(file_lines) < 3:
        raise ValueError("Environment file is incomplete.")

    # Line 1: grid size as "rows,columns"
    num_rows, num_cols = (int(number) for number in file_lines[0].split(','))
    line_number = 1

    # Obstacles: a count, then that many coordinates
    num_obstacles = int(file_lines[line_number]); line_number += 1
    if num_obstacles:
        obstacle_coordinates, line_number = _read_coordinates(file_lines, line_number, num_obstacles)
    else:
        obstacle_coordinates = []

    # Dirty locations: a count, then that many coordinates (first = D1, second = D2, ...)
    num_dirty = int(file_lines[line_number]); line_number += 1
    if num_dirty:
        dirty_locations, line_number = _read_coordinates(file_lines, line_number, num_dirty)
    else:
        dirty_locations = []

    # Charging station: exactly one coordinate
    (charging_station,), line_number = _read_coordinates(file_lines, line_number, 1)
    obstacle_set = set(obstacle_coordinates)   # a set makes "is this cell blocked?" checks fast

    # ---- Validate the input ----
    if len(obstacle_set) != num_obstacles:
        raise ValueError("Duplicate obstacle coordinates found.")
    if len(set(dirty_locations)) != num_dirty:
        raise ValueError("Duplicate dirty-location coordinates found.")
    cells_to_check = (("Obstacle", obstacle_coordinates),
                      ("Dirty location", dirty_locations),
                      ("Charging station", [charging_station]))
    for cell_kind, cell_list in cells_to_check:
        for (row, col) in cell_list:
            if not (0 <= row < num_rows and 0 <= col < num_cols):
                raise ValueError(f"{cell_kind} {(row, col)} lies outside the {num_rows}x{num_cols} grid.")
    if charging_station in obstacle_set:
        raise ValueError("Charging station overlaps an obstacle.")
    if charging_station in dirty_locations:
        raise ValueError("Charging station overlaps a dirty location.")
    dirty_on_obstacle = obstacle_set.intersection(dirty_locations)
    if dirty_on_obstacle:
        raise ValueError(f"Dirty location(s) overlap obstacles: {sorted(dirty_on_obstacle)}")

    # The keys 'rows', 'cols', 'obstacles' are used by the search code, so keep these names.
    grid = {'rows': num_rows, 'cols': num_cols, 'obstacles': obstacle_set}
    return grid, charging_station, dirty_locations


def parse_schedule(file_path, num_dirty):
    """Load Schedule.txt. Accepts pairs written as `D1,D3` or `(D1,D3)`. Raises ValueError if invalid."""
    with open(file_path, 'r', encoding='utf-8') as schedule_file:
        file_lines = [text.strip() for text in schedule_file if text.strip()]   # ignore blank lines

    def split_pair(line_text):
        """Turn a line like '(D1,D3)' or 'D1, D3' into ('D1', 'D3')."""
        pieces = re.sub(r'[()\s]', '', line_text).split(',')   # drop brackets and spaces
        if len(pieces) != 2 or not all(pieces):
            raise ValueError(f"Malformed constraint line: '{line_text}'")
        return pieces[0].upper(), pieces[1].upper()

    # Line 1: number of time slots
    num_slots = int(file_lines[0])
    if num_slots < num_dirty:
        raise ValueError(f"Number of time slots ({num_slots}) must be >= number of dirty locations ({num_dirty})")

    # Precedence constraints: (task_first, task_second) means task_first is cleaned before task_second
    num_precedence = int(file_lines[1])
    line_number = 2
    precedence_pairs = []
    for _ in range(num_precedence):
        precedence_pairs.append(split_pair(file_lines[line_number]))
        line_number += 1

    # Latest-slot constraints: (task, slot) means the task must be done in that slot or earlier
    num_latest_slot = int(file_lines[line_number]); line_number += 1
    latest_slot_pairs = []
    for _ in range(num_latest_slot):
        latest_slot_pairs.append(split_pair(file_lines[line_number]))
        line_number += 1

    # ---- Validate labels: every task must be one of D1..Dn, every slot must look like T1, T2, ... ----
    valid_task_labels = {f"D{number + 1}" for number in range(num_dirty)}
    for task_first, task_second in precedence_pairs:
        for task_label in (task_first, task_second):
            if task_label not in valid_task_labels:
                raise ValueError(f"Precedence constraint refers to unknown task '{task_label}' (valid: D1..D{num_dirty}).")
    for task_label, slot_label in latest_slot_pairs:
        if task_label not in valid_task_labels:
            raise ValueError(f"Latest-slot constraint refers to unknown task '{task_label}' (valid: D1..D{num_dirty}).")
        if not re.fullmatch(r'T\d+', slot_label):
            raise ValueError(f"Invalid slot label '{slot_label}' (expected T1, T2, ...).")

    # The keys 'precedence', 'latest_slots', 'num_slots' are used by the CSP code, so keep these names.
    return {'precedence': precedence_pairs, 'latest_slots': latest_slot_pairs, 'num_slots': num_slots}


def get_successors(state, grid):
    """Return every cell the robot can step to from `state`, as (next_state, action, step_cost)."""
    row, col = state
    successors = []
    # The four allowed moves: (row change, column change, action name). No diagonals.
    possible_moves = [(-1, 0, 'UP'), (1, 0, 'DOWN'), (0, -1, 'LEFT'), (0, 1, 'RIGHT')]

    for row_change, col_change, action in possible_moves:
        next_row, next_col = row + row_change, col + col_change
        inside_grid = 0 <= next_row < grid['rows'] and 0 <= next_col < grid['cols']
        if inside_grid and (next_row, next_col) not in grid['obstacles']:
            successors.append(((next_row, next_col), action, 1))   # every step costs 1
    return successors


def goal_test(state, goal_state):
    """True if the robot is standing on the goal cell."""
    return state == goal_state


def path_cost(path):
    """Total cost of a path = number of moves = number of cells - 1 (0 for an empty or 1-cell path)."""
    if not path or len(path) <= 1:
        return 0
    return len(path) - 1


def heuristic(state, goal_state):
    """Manhattan distance: the fewest moves needed if there were no obstacles."""
    return abs(state[0] - goal_state[0]) + abs(state[1] - goal_state[1])


def search(grid, start, goal, algorithm="A*"):
    """Find a path from `start` to `goal`. Returns (path, cost, nodes_expanded).
    If no path exists it returns (None, infinity, nodes_expanded)."""
    if start == goal:
        return [start], 0, 0

    nodes_expanded = 0   # how many cells we pulled off the frontier and explored

    if algorithm == "BFS":
        # Breadth-First Search: explore cells in rings of equal distance, using a first-in-first-out queue.
        frontier = deque([(start, [start])])   # each entry: (cell, path taken to reach it)
        visited_cells = {start}

        while frontier:
            current_cell, path_so_far = frontier.popleft()
            nodes_expanded += 1

            for next_cell, action, step_cost in get_successors(current_cell, grid):
                if next_cell not in visited_cells:
                    extended_path = path_so_far + [next_cell]
                    if goal_test(next_cell, goal):
                        return extended_path, path_cost(extended_path), nodes_expanded
                    visited_cells.add(next_cell)
                    frontier.append((next_cell, extended_path))

    else:
        # A* Search: always expand the cell with the lowest f = cost_so_far + estimated_cost_to_goal.
        # Each frontier entry: (f_score, cell, path taken to reach it, cost_so_far)
        start_estimate = heuristic(start, goal)
        frontier = [(start_estimate, start, [start], 0)]
        best_cost_to_cell = {}   # cheapest known cost to reach each expanded cell

        while frontier:
            f_score, current_cell, path_so_far, cost_so_far = heapq.heappop(frontier)

            # Skip this entry if we already reached the same cell at an equal or lower cost
            if current_cell in best_cost_to_cell and best_cost_to_cell[current_cell] <= cost_so_far:
                continue

            best_cost_to_cell[current_cell] = cost_so_far
            nodes_expanded += 1

            if goal_test(current_cell, goal):
                return path_so_far, path_cost(path_so_far), nodes_expanded

            for next_cell, action, step_cost in get_successors(current_cell, grid):
                new_cost = cost_so_far + step_cost
                if next_cell not in best_cost_to_cell or best_cost_to_cell[next_cell] > new_cost:
                    new_f_score = new_cost + heuristic(next_cell, goal)
                    heapq.heappush(frontier, (new_f_score, next_cell, path_so_far + [next_cell], new_cost))

    return None, float('inf'), nodes_expanded   # frontier ran out: no path exists


def select_unassigned_variable(assignment, domains, constraints):
    """Choose which task to assign next: MRV first, then the degree heuristic to break ties."""
    unassigned_tasks = [task for task in domains.keys() if task not in assignment]

    # MRV (Minimum Remaining Values): find the task(s) with the fewest slots still possible
    tied_tasks = []
    fewest_slots_left = float('inf')
    for task in unassigned_tasks:
        slots_left = len(domains[task])
        if slots_left < fewest_slots_left:
            fewest_slots_left = slots_left
            tied_tasks = [task]            # new record low: start a fresh list
        elif slots_left == fewest_slots_left:
            tied_tasks.append(task)        # ties the record: keep it as well

    if len(tied_tasks) == 1:
        return tied_tasks[0]

    # Degree heuristic (tie-break): pick the task linked to the most other unassigned tasks
    highest_degree = -1
    best_task = tied_tasks[0]

    for task in tied_tasks:
        degree = 0
        for task_first, task_second in constraints['precedence']:
            # A precedence rule counts only if the task at its other end is still unassigned
            if (task_first == task and task_second not in assignment) or \
               (task_second == task and task_first not in assignment):
                degree += 1
        # Slot exclusivity links this task to every other unassigned task
        degree += len(unassigned_tasks) - 1

        if degree > highest_degree:
            highest_degree = degree
            best_task = task

    return best_task


def order_domain_values(variable, assignment, domains, constraints):
    """LCV (Least Constraining Value): order the slots so the one that removes the
    fewest options from the other unassigned tasks is tried first."""
    candidate_slots = domains[variable]
    if len(candidate_slots) <= 1:
        return candidate_slots

    other_unassigned_tasks = [task for task in domains.keys() if task not in assignment and task != variable]

    def count_eliminated(slot):
        """How many options for the other tasks would be ruled out if `variable` took `slot`?"""
        eliminated = 0
        slot_number = int(slot[1:])                       # 'T3' -> 3
        for other_task in other_unassigned_tasks:
            for other_slot in domains[other_task]:
                if other_slot == slot:                    # exclusivity: that slot would be taken
                    eliminated += 1
                    continue
                other_slot_number = int(other_slot[1:])
                for task_first, task_second in constraints['precedence']:
                    if variable == task_first and other_task == task_second:
                        if not (slot_number < other_slot_number):
                            eliminated += 1               # other_task would no longer be after variable
                    elif variable == task_second and other_task == task_first:
                        if not (other_slot_number < slot_number):
                            eliminated += 1               # other_task would no longer be before variable
        return eliminated

    return sorted(candidate_slots, key=count_eliminated)   # fewest eliminated first


def is_consistent(variable, value, assignment, constraints):
    """Can `variable` take slot `value` without breaking any constraint that can be checked right now?"""
    # 1. Slot exclusivity: no other task may already use this slot
    if value in assignment.values():
        return False

    slot_number = int(value[1:])

    # 2. Latest-slot constraint: the slot must not be later than the task's limit
    for task, latest_slot in constraints['latest_slots']:
        if task == variable:
            if slot_number > int(latest_slot[1:]):
                return False

    # 3. Precedence constraints: only checked against tasks that are already assigned
    for task_first, task_second in constraints['precedence']:
        if variable == task_first and task_second in assignment:
            second_slot_number = int(assignment[task_second][1:])
            if not (slot_number < second_slot_number):
                return False
        elif variable == task_second and task_first in assignment:
            first_slot_number = int(assignment[task_first][1:])
            if not (first_slot_number < slot_number):
                return False

    return True


def forward_check(variable, value, assignment, domains, constraints):
    """After assigning `value` to `variable`, remove slots that are no longer possible for the
    unassigned tasks. Returns the updated domains, or None if some task has no slot left."""
    updated_domains = copy.deepcopy(domains)     # work on a copy so backtracking can simply discard it
    assigned_slot_number = int(value[1:])

    for other_task in domains.keys():
        if other_task in assignment:             # already decided: nothing to prune
            continue

        allowed_slots = []
        for other_slot in updated_domains[other_task]:
            other_slot_number = int(other_slot[1:])

            # Rule 1: slot exclusivity - the slot just used is no longer available
            if other_slot == value:
                continue

            # Rule 2: precedence - drop slots that would break an ordering rule with `variable`
            slot_allowed = True
            for task_first, task_second in constraints['precedence']:
                if variable == task_first and other_task == task_second:
                    if not (assigned_slot_number < other_slot_number):
                        slot_allowed = False
                        break
                elif variable == task_second and other_task == task_first:
                    if not (other_slot_number < assigned_slot_number):
                        slot_allowed = False
                        break

            if slot_allowed:
                allowed_slots.append(other_slot)

        if not allowed_slots:                    # empty domain: this choice is a dead end
            return None
        updated_domains[other_task] = allowed_slots

    return updated_domains


def prune_latest_slots(domains, constraints):
    """Node consistency: remove slots later than a task's latest-slot constraint (unary constraints)."""
    pruned_domains = {task: list(slots) for task, slots in domains.items()}
    for task, latest_slot in constraints['latest_slots']:
        latest_slot_number = int(latest_slot[1:])
        pruned_domains[task] = [slot for slot in pruned_domains[task] if int(slot[1:]) <= latest_slot_number]
    return pruned_domains


def backtracking_search(variables, domains, constraints):
    """Find a complete task -> slot assignment, or return None if no feasible schedule exists."""

    def backtrack(assignment, current_domains):
        if len(assignment) == len(variables):         # every task has a slot: done
            return assignment

        next_task = select_unassigned_variable(assignment, current_domains, constraints)   # MRV + degree
        for slot in order_domain_values(next_task, assignment, current_domains, constraints):   # LCV order
            if is_consistent(next_task, slot, assignment, constraints):
                assignment[next_task] = slot

                # Forward checking works on a copy, so the old domains are restored automatically on backtrack
                domains_after_check = forward_check(next_task, slot, assignment, current_domains, constraints)
                if domains_after_check is not None:
                    complete_assignment = backtrack(assignment, domains_after_check)
                    if complete_assignment is not None:
                        return complete_assignment

                del assignment[next_task]             # dead end: undo this choice and try the next slot
        return None

    domains = prune_latest_slots(domains, constraints)
    if any(len(slots) == 0 for slots in domains.values()):
        return None                                   # a task has no feasible slot at all
    return backtrack({}, domains)


def verify_schedule(assignment, constraints):
    if len(set(assignment.values())) != len(assignment):
        return False
    for before, after in constraints["precedence"]:
        if int(assignment[before][1:]) >= int(assignment[after][1:]):
            return False
    for task, latest_slot in constraints["latest_slots"]:
        if int(assignment[task][1:]) > int(latest_slot[1:]):
            return False
    return True


class VacuumRobotGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Intelligent Vacuum-Cleaning Robot")
        width = min(1280, self.winfo_screenwidth() - 40)
        height = min(820, self.winfo_screenheight() - 100)
        self.geometry(f"{width}x{height}")
        self.minsize(900, 680)
        self.configure(bg="#eef2f7")

        self.project_dir = Path(__file__).resolve().parent
        self.scenarios = {
            "Experiment": ("Environment.txt", "Schedule.txt"),
            "Sample": ("Environment_Sample.txt", "Schedule_Sample.txt"),
            "CSP Failure": ("Environment.txt", "Schedule_Error.txt"),
            "Navigation Failure": ("Environment_Error.txt", "Schedule_Sample.txt"),
        }
        self.scenario = tk.StringVar(value="Experiment")
        self.environment_path = tk.StringVar()
        self.schedule_path = tk.StringVar()
        self.algorithm = tk.StringVar(value="A*")
        self.status_message = tk.StringVar(value="Ready to run a simulation.")
        self._grid_state = None
        self._frame = {}                     # what the robot animation is currently showing
        self._anim = None                    # path/stops/step of the current animation
        self._anim_job = None                # pending Tk `after` callback
        self.speed = tk.IntVar(value=5)      # animation speed, 1 (slow) to 10 (fast)
        self.step_text = tk.StringVar(value="")

        self._configure_styles()
        self._select_scenario()

        self._build_ui()

    def _configure_styles(self):
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        style.configure("App.TFrame", background="#eef2f7")
        style.configure("Panel.TFrame", background="#ffffff")
        style.configure(
            "Panel.TLabelframe",
            background="#ffffff",
            bordercolor="#d8e0eb",
            relief="solid",
        )
        style.configure(
            "Panel.TLabelframe.Label",
            background="#ffffff",
            foreground="#23324d",
            font=("Segoe UI", 10, "bold"),
        )
        style.configure(
            "TLabel",
            background="#ffffff",
            foreground="#34445f",
            font=("Segoe UI", 9),
        )
        style.configure(
            "Hint.TLabel",
            background="#ffffff",
            foreground="#718096",
            font=("Segoe UI", 8),
        )
        style.configure("TEntry", padding=7)
        style.configure("TCombobox", padding=6)
        style.configure("TRadiobutton", background="#ffffff", foreground="#34445f")
        style.configure(
            "TButton",
            padding=(10, 7),
            background="#e8edf5",
            foreground="#23324d",
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "TButton",
            background=[("active", "#d9e2ef"), ("pressed", "#cad6e5")],
        )
        style.configure(
            "Accent.TButton",
            padding=(16, 9),
            background="#176b87",
            foreground="#ffffff",
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Accent.TButton",
            background=[("active", "#125a72"), ("pressed", "#0f4e63")],
            foreground=[("disabled", "#dbe6eb")],
        )
        style.configure(
            "Treeview",
            background="#ffffff",
            fieldbackground="#ffffff",
            foreground="#283650",
            rowheight=24,
            bordercolor="#e2e8f0",
            font=("Segoe UI", 9),
        )
        style.configure(
            "Treeview.Heading",
            background="#f1f5f9",
            foreground="#34445f",
            relief="flat",
            font=("Segoe UI", 9, "bold"),
            padding=6,
        )
        style.map("Treeview.Heading", background=[("active", "#e7edf5")])

    def _build_ui(self):
        main = ttk.Frame(self, padding=12, style="App.TFrame")
        main.pack(fill="both", expand=True)

        # ---- Compact header: title and subtitle on one line ----
        header = tk.Frame(main, bg="#172943", padx=18, pady=6)
        header.pack(fill="x", pady=(0, 8))
        tk.Label(
            header,
            text="VACUUM ROBOT",
            bg="#172943",
            fg="#ffffff",
            font=("Segoe UI", 16, "bold"),
        ).pack(side="left")
        tk.Label(
            header,
            text="Constraint-based cleaning schedule  |  Intelligent grid navigation",
            bg="#172943",
            fg="#c5d2e3",
            font=("Segoe UI", 10),
        ).pack(side="left", padx=(18, 0), pady=(5, 0))

        # ---- Controls: row 1 = scenario / algorithm / run, row 2 = input files ----
        controls = ttk.Frame(main, padding=8, style="Panel.TFrame")
        controls.pack(fill="x", pady=(0, 8))

        top_row = ttk.Frame(controls, style="Panel.TFrame")
        top_row.pack(fill="x")
        ttk.Label(top_row, text="Scenario").pack(side="left", padx=(0, 6))
        self.scenario_picker = ttk.Combobox(
            top_row,
            textvariable=self.scenario,
            values=(
                "Experiment",
                "Sample",
                "CSP Failure",
                "Navigation Failure",
                "Custom files",
            ),
            state="readonly",
            width=20,
        )
        self.scenario_picker.pack(side="left")
        self.scenario_picker.bind("<<ComboboxSelected>>", self._select_scenario)

        ttk.Label(top_row, text="Navigation algorithm").pack(side="left", padx=(28, 8))
        ttk.Radiobutton(
            top_row, text="A* Search", variable=self.algorithm, value="A*"
        ).pack(side="left", padx=(0, 10))
        ttk.Radiobutton(
            top_row, text="BFS", variable=self.algorithm, value="BFS"
        ).pack(side="left")

        ttk.Button(
            top_row,
            text="Run simulation",
            style="Accent.TButton",
            command=self.run_simulation,
        ).pack(side="right")

        paths = ttk.Frame(controls, style="Panel.TFrame")
        paths.pack(fill="x", pady=(8, 0))
        ttk.Label(paths, text="Environment file").grid(row=0, column=0, sticky="w")
        ttk.Entry(paths, textvariable=self.environment_path, state="readonly").grid(
            row=1, column=0, sticky="ew", padx=(0, 6)
        )
        ttk.Button(
            paths, text="Browse...", command=self._choose_environment
        ).grid(row=1, column=1, padx=(0, 16))
        ttk.Label(paths, text="Schedule file").grid(row=0, column=2, sticky="w")
        ttk.Entry(paths, textvariable=self.schedule_path, state="readonly").grid(
            row=1, column=2, sticky="ew", padx=(0, 6)
        )
        ttk.Button(
            paths, text="Browse...", command=self._choose_schedule
        ).grid(row=1, column=3)
        paths.columnconfigure(0, weight=1)
        paths.columnconfigure(2, weight=1)

        # ---- Main area: map on the left, results on the right ----
        content = ttk.Panedwindow(main, orient=tk.HORIZONTAL)
        content.pack(fill="both", expand=True)

        left = ttk.LabelFrame(
            content, text="Environment map", padding=10, style="Panel.TLabelframe"
        )
        right = ttk.LabelFrame(
            content, text="Simulation results", padding=10, style="Panel.TLabelframe"
        )
        content.add(left, weight=3)
        content.add(right, weight=2)

        legend = ttk.Frame(left, style="Panel.TFrame")
        legend.pack(fill="x", pady=(0, 6))
        for label, color in (
            ("Station", "#45b7a3"),
            ("Dirty", "#f3c969"),
            ("Cleaned", "#8fd19e"),
            ("Robot", "#e4572e"),
            ("Path", "#a9d8cd"),
            ("Obstacle", "#394867"),
        ):
            swatch = tk.Canvas(
                legend, width=12, height=12, bg="#ffffff", highlightthickness=0
            )
            swatch.pack(side="left", padx=(0, 4))
            swatch.create_rectangle(1, 1, 11, 11, fill=color, outline=color)
            ttk.Label(legend, text=label, style="Hint.TLabel").pack(
                side="left", padx=(0, 9)
            )

        # Animation controls (packed first, at the bottom, so the canvas keeps the remaining space)
        anim_bar = ttk.Frame(left, style="Panel.TFrame")
        anim_bar.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Button(anim_bar, text="Replay", command=self._replay).pack(side="left")
        ttk.Label(anim_bar, text="Speed").pack(side="left", padx=(14, 4))
        ttk.Scale(
            anim_bar, from_=1, to=10, orient="horizontal", length=140,
            variable=self.speed,
        ).pack(side="left")
        ttk.Label(anim_bar, textvariable=self.step_text, style="Hint.TLabel").pack(
            side="right"
        )

        self.canvas = tk.Canvas(left, bg="#f7f9fc", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._redraw_grid)

        # ---- Results panel (grid: the two tables share whatever height is left) ----
        right.columnconfigure(0, weight=1)
        status_label = ttk.Label(
            right, textvariable=self.status_message, style="Hint.TLabel", wraplength=300
        )
        status_label.grid(row=0, column=0, sticky="w", pady=(0, 6))
        # keep the status text wrapped to the width of the panel
        right.bind(
            "<Configure>",
            lambda event: status_label.configure(wraplength=max(event.width - 40, 120)),
        )

        # Summary cards (2 rows x 3 columns) instead of a tall metrics table
        self.metric_vars = {}
        cards = ttk.Frame(right, style="Panel.TFrame")
        cards.grid(row=1, column=0, sticky="ew")
        for position, (key, caption) in enumerate((
            ("Algorithm", "Algorithm"),
            ("Status", "Status"),
            ("Schedule Valid", "Schedule valid"),
            ("Total Movement Cost", "Move cost"),
            ("Total Nodes Expanded", "Nodes"),
        )):
            card = tk.Frame(cards, bg="#f1f5f9", padx=8, pady=3)
            card.grid(
                row=position // 3, column=position % 3, sticky="ew",
                padx=(0 if position % 3 == 0 else 6, 0), pady=(0, 5),
            )
            tk.Label(
                card, text=caption, bg="#f1f5f9", fg="#718096", font=("Segoe UI", 8)
            ).pack(anchor="w")
            value = tk.StringVar(value=self.EMPTY_METRIC)
            self.metric_vars[key] = value
            tk.Label(
                card, textvariable=value, bg="#f1f5f9", fg="#23324d",
                font=("Segoe UI", 10, "bold"),
            ).pack(anchor="w")
        cards.columnconfigure(0, weight=2)
        cards.columnconfigure(1, weight=3)    # Status: the longest text
        cards.columnconfigure(2, weight=2)

        ttk.Label(right, text="Cleaning schedule").grid(row=2, column=0, sticky="w", pady=(2, 0))
        self.schedule_table = self._make_table(
            right, 3,
            (("slot", "Time slot", 105, False),
             ("task", "Task", 70, False),
             ("location", "Location", 120, True)),
        )

        ttk.Label(right, text="Navigation legs").grid(row=4, column=0, sticky="w")
        self.leg_table = self._make_table(
            right, 5,
            (("leg", "#", 36, False),
             ("from", "From", 120, True),
             ("to", "To", 150, True),
             ("cost", "Cost", 62, False),
             ("nodes", "Nodes", 72, False)),
        )
        right.rowconfigure(3, weight=1)   # schedule table gets 1 share of the spare height
        right.rowconfigure(5, weight=2)   # legs table gets 2 shares (it usually has more rows)

    EMPTY_METRIC = "\u2014"

    def _make_table(self, parent, row, columns):
        """A table with a vertical scrollbar, placed in `row` of `parent`'s grid.
        columns = ((key, heading, width, stretch), ...)."""
        frame = ttk.Frame(parent, style="Panel.TFrame")
        table = ttk.Treeview(
            frame, columns=[column[0] for column in columns],
            show="headings", height=2,
        )
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=table.yview)
        table.configure(yscrollcommand=scrollbar.set)
        for key, heading, width, stretch in columns:
            table.heading(key, text=heading, anchor="w")
            table.column(key, width=width, minwidth=30, stretch=stretch, anchor="w")
        table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        frame.grid(row=row, column=0, sticky="nsew", pady=(3, 8))
        return table

    def _select_scenario(self, _event=None):
        selected = self.scenario.get()
        files = self.scenarios.get(selected)
        if files is None:
            return

        environment = self.project_dir / files[0]
        schedule = self.project_dir / files[1]
        missing = [path.name for path in (environment, schedule) if not path.is_file()]
        if missing:
            message = f"Scenario files are missing: {', '.join(missing)}"
            self.status_message.set(message)
            messagebox.showerror("Scenario files missing", message)
            self.scenario.set("Custom files")
            return

        self.environment_path.set(str(environment))
        self.schedule_path.set(str(schedule))
        self.status_message.set(f"{selected} scenario selected.")

    def _choose_environment(self):
        path = filedialog.askopenfilename(
            title="Select environment file",
            initialdir=str(self.project_dir),
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.environment_path.set(path)
            self.scenario.set("Custom files")

    def _choose_schedule(self):
        path = filedialog.askopenfilename(
            title="Select schedule file",
            initialdir=str(self.project_dir),
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.schedule_path.set(path)
            self.scenario.set("Custom files")

    def _redraw_grid(self, _event=None):
        if self._grid_state:
            self.draw_grid(*self._grid_state, **self._frame)

    def _stop_animation(self):
        if self._anim_job is not None:
            self.after_cancel(self._anim_job)
            self._anim_job = None

    def _start_animation(self, path, dirty_stops, failed_target=None):
        """Animate the robot along `path`. dirty_stops = [(index in path, cell)] marks when each
        dirty cell is reached (and therefore cleaned); failed_target is shown in red at the end."""
        self._stop_animation()
        self._anim = {
            "path": path,
            "stops": dirty_stops,
            "failed": failed_target,
            "step": 0,
        }
        self._animate_step()

    def _animate_step(self):
        anim = self._anim
        path, step = anim["path"], anim["step"]
        last_step = len(path) - 1
        self._frame = {
            "robot": path[step],
            "trail": set(path[: step + 1]),
            "cleaned": {cell for index, cell in anim["stops"] if index <= step},
            "failed": anim["failed"] if step == last_step else None,
        }
        self.step_text.set(f"Move {step} / {last_step}")
        self.draw_grid(*self._grid_state, **self._frame)

        if step < last_step:
            anim["step"] += 1
            delay_ms = int(900 / self.speed.get())     # speed 1 -> 900 ms, speed 10 -> 90 ms
            self._anim_job = self.after(delay_ms, self._animate_step)
        else:
            self._anim_job = None

    def _replay(self):
        if self._anim and self._grid_state:
            self._anim["step"] = 0
            self._stop_animation()
            self._animate_step()

    def run_simulation(self):
        environment = self.environment_path.get()
        schedule_file = self.schedule_path.get()
        self._stop_animation()
        self._anim = None
        self._frame = {}
        self.step_text.set("")
        self._clear_tables()
        self._grid_state = None
        self.canvas.delete("all")
        if not environment or not schedule_file:
            messagebox.showwarning(
                "Missing files", "Please select both an environment and schedule file."
            )
            return

        try:
            grid, charging_station, dirty_locations = parse_environment(environment)
            constraints = parse_schedule(schedule_file, len(dirty_locations))
        except Exception as error:
            self.status_message.set(f"Input error: {error}")
            messagebox.showerror("Input error", str(error))
            return

        variables = [f"D{i + 1}" for i in range(len(dirty_locations))]
        domains = {
            variable: [f"T{slot + 1}" for slot in range(constraints["num_slots"])]
            for variable in variables
        }
        assignment = backtracking_search(variables, domains, constraints)

        if assignment is None:
            self.status_message.set("CSP failure: no feasible schedule.")
            self._add_metric("Status", "No feasible schedule")
            self._grid_state = (grid, charging_station, dirty_locations, None)
            self.draw_grid(grid, charging_station, dirty_locations)
            return

        sorted_assignment = sorted(
            assignment.items(), key=lambda item: int(item[1][1:])
        )
        task_map = {
            f"D{i + 1}": dirty_locations[i]
            for i in range(len(dirty_locations))
        }
        schedule = [(task, task_map[task]) for task, _slot in sorted_assignment]

        for task, slot in sorted_assignment:
            self.schedule_table.insert(
                "", "end",
                values=(slot, task, str(task_map[task])),
            )

        complete_path = [charging_station]
        dirty_stops = []          # (index in complete_path, cell) each time a dirty cell is reached
        failed_target = None      # the cell that could not be reached, if any
        current = charging_station
        legs = []
        total_cost = 0
        total_nodes = 0
        for task, target in schedule:
            path, cost, nodes = search(
                grid, current, target, self.algorithm.get()
            )
            total_nodes += nodes
            if path is None:
                legs.append(
                    {"from": str(current), "to": f"{task} {target}", "cost": "N/A",
                     "nodes": nodes}
                )
                failed_target = target
                break
            legs.append(
                {"from": str(current), "to": f"{task} {target}",
                 "cost": cost, "nodes": nodes}
            )
            complete_path.extend(path[1:])
            dirty_stops.append((len(complete_path) - 1, target))
            total_cost += cost
            current = target
        else:
            path, cost, nodes = search(
                grid, current, charging_station, self.algorithm.get()
            )
            total_nodes += nodes
            if path is None:
                legs.append(
                    {"from": str(current), "to": "Charging Station",
                     "cost": "N/A", "nodes": nodes}
                )
                failed_target = charging_station
            else:
                legs.append(
                    {"from": str(current),
                     "to": f"Charging Station {charging_station}",
                     "cost": cost, "nodes": nodes}
                )
                complete_path.extend(path[1:])
                total_cost += cost

        success = all(
            leg["cost"] != "N/A" for leg in legs
        ) and len(legs) == len(schedule) + 1
        if success:
            self.status_message.set(
                f"Success — cleaned all locations and returned to charging station."
            )
        else:
            self.status_message.set("Navigation failure — a target is unreachable.")

        for index, leg in enumerate(legs, start=1):
            self.leg_table.insert(
                "", "end",
                values=(index, leg["from"], leg["to"], leg["cost"], leg["nodes"]),
            )

        self._add_metric("Algorithm", self.algorithm.get())
        self._add_metric("Status", "Success" if success else "Navigation failure")
        self._add_metric("Schedule Valid", str(verify_schedule(assignment, constraints)))
        self._add_metric("Total Movement Cost", str(total_cost) if success else "N/A")
        self._add_metric("Total Nodes Expanded", str(total_nodes))

        for table in (self.schedule_table, self.leg_table):
            table.yview_moveto(0)
            self.after_idle(table.yview_moveto, 0)

        # The map is drawn by the animation, which shows the robot's trail as it moves.
        # On a navigation failure the robot still drives the legs that worked, then stops.
        self._grid_state = (grid, charging_station, dirty_locations, None)
        self._start_animation(complete_path, dirty_stops, failed_target)

    def _clear_tables(self):
        for table in (self.schedule_table, self.leg_table):
            for item in table.get_children():
                table.delete(item)
        for value in self.metric_vars.values():
            value.set(self.EMPTY_METRIC)

    def _add_metric(self, metric, value):
        self.metric_vars[metric].set(value)

    def draw_grid(self, grid, charging_station, dirty_locations, path=None,
                  robot=None, trail=None, cleaned=None, failed=None):
        canvas = self.canvas
        canvas.delete("all")
        canvas.update_idletasks()
        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), 1)
        rows = grid["rows"]
        cols = grid["cols"]
        cell = min(width / cols, height / rows)
        offset_x = (width - cols * cell) / 2
        offset_y = (height - rows * cell) / 2
        path_set = set(trail) if trail is not None else set(path or [])
        cleaned = cleaned or set()

        for row in range(rows):
            for col in range(cols):
                position = (row, col)
                x1 = offset_x + col * cell
                y1 = offset_y + row * cell
                x2 = x1 + cell
                y2 = y1 + cell
                fill = "#ffffff"
                if position in grid["obstacles"]:
                    fill = "#394867"
                elif position == charging_station:
                    fill = "#45b7a3"
                elif position == failed:
                    fill = "#e57373"          # unreachable target
                elif position in dirty_locations:
                    fill = "#8fd19e" if position in cleaned else "#f3c969"
                elif position in path_set:
                    fill = "#a9d8cd"
                canvas.create_rectangle(
                    x1, y1, x2, y2, fill=fill, outline="#d9dee8"
                )

                if position == charging_station:
                    text = "C"
                elif position in dirty_locations:
                    text = f"D{dirty_locations.index(position) + 1}"
                    if position in cleaned:
                        text += " ✓"
                    elif position == failed:
                        text += " ✗"
                else:
                    text = ""
                if text:
                    robot_here = position == robot
                    canvas.create_text(
                        (x1 + x2) / 2,
                        y1 + cell * (0.78 if robot_here else 0.5),
                        text=text,
                        fill="#17223b",
                        font=("Segoe UI", max(7, int(cell / (5 if robot_here else 4))), "bold"),
                    )

        if robot is not None:
            # The robot: a red disc on its current cell (drawn last so it sits on top).
            # On a labelled cell (C, D1, ...) it is smaller and sits above the label.
            labelled = robot == charging_station or robot in dirty_locations
            size = cell * (0.40 if labelled else 0.64)
            centre_x = offset_x + (robot[1] + 0.5) * cell
            centre_y = offset_y + (robot[0] + (0.34 if labelled else 0.5)) * cell
            canvas.create_oval(
                centre_x - size / 2, centre_y - size / 2,
                centre_x + size / 2, centre_y + size / 2,
                fill="#e4572e", outline="#ffffff", width=2,
            )


if __name__ == "__main__":
    app = VacuumRobotGUI()
    app.mainloop()
