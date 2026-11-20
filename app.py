import copy
import heapq
import re
import tkinter as tk
from collections import deque
from tkinter import filedialog, messagebox, ttk


def parse_environment(file_path):
    with open(file_path, "r", encoding="utf-8") as file:
        lines = [line.strip() for line in file if line.strip()]

    if len(lines) < 6:
        raise ValueError("Environment file is incomplete.")

    rows, cols = map(int, lines[0].split(","))
    num_obstacles = int(lines[1])
    obstacles = {
        (int(row), int(col))
        for row, col in re.findall(r"\((\d+),(\d+)\)", lines[2])
    }
    if len(obstacles) != num_obstacles:
        raise ValueError("The obstacle count does not match the obstacle coordinates.")

    num_dirty = int(lines[3])
    dirty_locations = [
        (int(row), int(col))
        for row, col in re.findall(r"\((\d+),(\d+)\)", lines[4])
    ]
    if len(dirty_locations) != num_dirty:
        raise ValueError("The dirty-location count does not match the coordinates.")

    charging_match = re.search(r"\((\d+),(\d+)\)", lines[5])
    if not charging_match:
        raise ValueError("Charging station coordinate is missing.")

    charging_station = (int(charging_match.group(1)), int(charging_match.group(2)))
    grid = {"rows": rows, "cols": cols, "obstacles": obstacles}
    return grid, charging_station, dirty_locations


def parse_schedule(file_path, num_dirty):
    with open(file_path, "r", encoding="utf-8") as file:
        lines = [line.strip() for line in file if line.strip()]

    num_slots = int(lines[0])
    if num_slots < num_dirty:
        raise ValueError(
            f"Number of time slots ({num_slots}) must be at least "
            f"the number of dirty locations ({num_dirty})."
        )

    num_precedence = int(lines[1])
    precedence = []
    line_index = 2
    for _ in range(num_precedence):
        before, after = (part.strip() for part in lines[line_index].split(",", 1))
        precedence.append((before, after))
        line_index += 1

    num_latest = int(lines[line_index])
    line_index += 1
    latest_slots = []
    for _ in range(num_latest):
        task, slot = (part.strip() for part in lines[line_index].split(",", 1))
        latest_slots.append((task, slot))
        line_index += 1

    return {
        "precedence": precedence,
        "latest_slots": latest_slots,
        "num_slots": num_slots,
    }


def get_successors(state, grid):
    row, col = state
    successors = []
    for dr, dc, action in (
        (-1, 0, "UP"),
        (1, 0, "DOWN"),
        (0, -1, "LEFT"),
        (0, 1, "RIGHT"),
    ):
        next_state = (row + dr, col + dc)
        next_row, next_col = next_state
        if (
            0 <= next_row < grid["rows"]
            and 0 <= next_col < grid["cols"]
            and next_state not in grid["obstacles"]
        ):
            successors.append((next_state, action, 1))
    return successors


def heuristic(state, goal):
    return abs(state[0] - goal[0]) + abs(state[1] - goal[1])


def search(grid, start, goal, algorithm="A*"):
    if start == goal:
        return [start], 0, 0

    nodes_expanded = 0
    if algorithm == "BFS":
        frontier = deque([(start, [start])])
        explored = {start}
        while frontier:
            state, path = frontier.popleft()
            nodes_expanded += 1
            for next_state, _action, _cost in get_successors(state, grid):
                if next_state in explored:
                    continue
                new_path = path + [next_state]
                if next_state == goal:
                    return new_path, len(new_path) - 1, nodes_expanded
                explored.add(next_state)
                frontier.append((next_state, new_path))
    else:
        frontier = [(heuristic(start, goal), start, [start], 0)]
        explored = {}
        while frontier:
            _score, state, path, cost = heapq.heappop(frontier)
            if state in explored and explored[state] <= cost:
                continue
            explored[state] = cost
            nodes_expanded += 1
            if state == goal:
                return path, cost, nodes_expanded
            for next_state, _action, step_cost in get_successors(state, grid):
                new_cost = cost + step_cost
                if next_state not in explored or explored[next_state] > new_cost:
                    score = new_cost + heuristic(next_state, goal)
                    heapq.heappush(
                        frontier,
                        (score, next_state, path + [next_state], new_cost),
                    )
    return None, float("inf"), nodes_expanded


def select_unassigned_variable(assignment, domains, constraints):
    unassigned = [variable for variable in domains if variable not in assignment]
    smallest = min(len(domains[variable]) for variable in unassigned)
    candidates = [
        variable for variable in unassigned if len(domains[variable]) == smallest
    ]
    if len(candidates) == 1:
        return candidates[0]

    def degree(variable):
        count = sum(
            1
            for before, after in constraints["precedence"]
            if (before == variable and after not in assignment)
            or (after == variable and before not in assignment)
        )
        return count + len(unassigned) - 1

    return max(candidates, key=degree)


def is_consistent(variable, value, assignment, constraints):
    if value in assignment.values():
        return False
    slot = int(value[1:])
    for task, latest_slot in constraints["latest_slots"]:
        if task == variable and slot > int(latest_slot[1:]):
            return False
    for before, after in constraints["precedence"]:
        if variable == before and after in assignment:
            if slot >= int(assignment[after][1:]):
                return False
        if variable == after and before in assignment:
            if int(assignment[before][1:]) >= slot:
                return False
    return True


def order_domain_values(variable, assignment, domains, constraints):
    unassigned = [
        neighbor
        for neighbor in domains
        if neighbor not in assignment and neighbor != variable
    ]

    def eliminated(value):
        count = 0
        value_slot = int(value[1:])
        for neighbor in unassigned:
            for neighbor_value in domains[neighbor]:
                if neighbor_value == value:
                    count += 1
                    continue
                neighbor_slot = int(neighbor_value[1:])
                for before, after in constraints["precedence"]:
                    if variable == before and neighbor == after:
                        if value_slot >= neighbor_slot:
                            count += 1
                    elif variable == after and neighbor == before:
                        if neighbor_slot >= value_slot:
                            count += 1
        return count

    return sorted(domains[variable], key=eliminated)


def forward_check(variable, value, assignment, domains, constraints):
    checked_domains = copy.deepcopy(domains)
    value_slot = int(value[1:])
    for neighbor in domains:
        if neighbor in assignment:
            continue
        remaining = []
        for neighbor_value in checked_domains[neighbor]:
            neighbor_slot = int(neighbor_value[1:])
            if neighbor_value == value:
                continue
            allowed = True
            for before, after in constraints["precedence"]:
                if variable == before and neighbor == after:
                    allowed = value_slot < neighbor_slot
                elif variable == after and neighbor == before:
                    allowed = neighbor_slot < value_slot
                if not allowed:
                    break
            if allowed:
                remaining.append(neighbor_value)
        if not remaining:
            return None
        checked_domains[neighbor] = remaining
    return checked_domains


def backtracking_search(variables, domains, constraints):
    def backtrack(assignment, current_domains):
        if len(assignment) == len(variables):
            return assignment
        variable = select_unassigned_variable(
            assignment, current_domains, constraints
        )
        for value in order_domain_values(
            variable, assignment, current_domains, constraints
        ):
            if is_consistent(variable, value, assignment, constraints):
                assignment[variable] = value
                new_domains = forward_check(
                    variable, value, assignment, current_domains, constraints
                )
                if new_domains is not None:
                    result = backtrack(assignment, new_domains)
                    if result is not None:
                        return result
                del assignment[variable]
        return None

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
        self.geometry("1180x720")
        self.minsize(900, 600)

        self.environment_path = tk.StringVar()
        self.schedule_path = tk.StringVar()
        self.algorithm = tk.StringVar(value="A*")
        self.status_message = tk.StringVar(value="Select files and run a simulation.")

        self._build_ui()

    def _build_ui(self):
        main = ttk.Frame(self, padding=12)
        main.pack(fill="both", expand=True)

        controls = ttk.LabelFrame(main, text="Scenario", padding=10)
        controls.pack(fill="x")

        ttk.Label(controls, text="Environment").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.environment_path, width=48).grid(
            row=0, column=1, padx=6
        )
        ttk.Button(
            controls, text="Browse...", command=self._choose_environment
        ).grid(row=0, column=2)

        ttk.Label(controls, text="Schedule").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(controls, textvariable=self.schedule_path, width=48).grid(
            row=1, column=1, padx=6, pady=(8, 0)
        )
        ttk.Button(
            controls, text="Browse...", command=self._choose_schedule
        ).grid(row=1, column=2, pady=(8, 0))

        algorithm_frame = ttk.Frame(controls)
        algorithm_frame.grid(row=0, column=3, rowspan=2, padx=(24, 0), sticky="ns")
        ttk.Label(algorithm_frame, text="Search").pack(anchor="w")
        ttk.Radiobutton(
            algorithm_frame, text="A*", variable=self.algorithm, value="A*"
        ).pack(anchor="w")
        ttk.Radiobutton(
            algorithm_frame, text="BFS", variable=self.algorithm, value="BFS"
        ).pack(anchor="w")

        ttk.Button(
            controls, text="Run Simulation", command=self.run_simulation
        ).grid(row=0, column=4, rowspan=2, padx=(18, 0), sticky="ns")
        controls.columnconfigure(1, weight=1)

        content = ttk.Frame(main)
        content.pack(fill="both", expand=True, pady=(10, 0))

        left = ttk.LabelFrame(content, text="Grid View", padding=8)
        left.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(left, bg="#f7f9fc", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        right = ttk.LabelFrame(content, text="Results", padding=8)
        right.pack(side="right", fill="both", expand=True)

        stats = ttk.Frame(right)
        stats.pack(fill="x")
        self.status_label = ttk.Label(stats, textvariable=self.status_message)
        self.status_label.pack(anchor="w")

        self.metrics = ttk.Treeview(
            stats,
            columns=("metric", "value"),
            show="headings",
            height=5,
        )
        self.metrics.heading("metric", text="Metric")
        self.metrics.heading("value", text="Value")
        self.metrics.column("metric", width=170)
        self.metrics.column("value", width=230)
        self.metrics.pack(fill="x", pady=(8, 8))

        ttk.Label(right, text="Cleaning Schedule").pack(anchor="w")
        self.schedule_table = ttk.Treeview(
            right,
            columns=("slot", "task", "location"),
            show="headings",
            height=8,
        )
        for column, heading in (
            ("slot", "Time Slot"),
            ("task", "Task"),
            ("location", "Location"),
        ):
            self.schedule_table.heading(column, text=heading)
        self.schedule_table.column("slot", width=90)
        self.schedule_table.column("task", width=90)
        self.schedule_table.column("location", width=150)
        self.schedule_table.pack(fill="x", pady=(4, 8))

        ttk.Label(right, text="Navigation Legs").pack(anchor="w")
        self.leg_table = ttk.Treeview(
            right,
            columns=("leg", "from", "to", "cost", "nodes"),
            show="headings",
            height=8,
        )
        for column, heading in (
            ("leg", "#"),
            ("from", "From"),
            ("to", "To"),
            ("cost", "Cost"),
            ("nodes", "Nodes"),
        ):
            self.leg_table.heading(column, text=heading)
            width = 55 if column in ("leg", "cost", "nodes") else 130
            self.leg_table.column(column, width=width)
        self.leg_table.pack(fill="both", expand=True, pady=(4, 0))

    def _choose_environment(self):
        path = filedialog.askopenfilename(
            title="Select environment file",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.environment_path.set(path)

    def _choose_schedule(self):
        path = filedialog.askopenfilename(
            title="Select schedule file",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.schedule_path.set(path)

    def run_simulation(self):
        environment = self.environment_path.get()
        schedule_file = self.schedule_path.get()
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

        self._clear_tables()
        if assignment is None:
            self.status_message.set("CSP failure: no feasible schedule.")
            self._add_metric("Status", "No feasible schedule")
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
        current = charging_station
        legs = []
        total_cost = 0
        total_nodes = 0
        for task, target in schedule:
            path, cost, nodes = search(
                grid, current, target, self.algorithm.get()
            )
            if path is None:
                legs.append(
                    {"from": str(current), "to": f"{task} {target}", "cost": "N/A",
                     "nodes": nodes}
                )
                break
            legs.append(
                {"from": str(current), "to": f"{task} {target}",
                 "cost": cost, "nodes": nodes}
            )
            complete_path.extend(path[1:])
            total_cost += cost
            total_nodes += nodes
            current = target
        else:
            path, cost, nodes = search(
                grid, current, charging_station, self.algorithm.get()
            )
            if path is None:
                legs.append(
                    {"from": str(current), "to": "Charging Station",
                     "cost": "N/A", "nodes": nodes}
                )
            else:
                legs.append(
                    {"from": str(current),
                     "to": f"Charging Station {charging_station}",
                     "cost": cost, "nodes": nodes}
                )
                complete_path.extend(path[1:])
                total_cost += cost
                total_nodes += nodes

        success = all(
            leg["cost"] != "N/A" for leg in legs
        ) and len(legs) == len(schedule) + 1
        if success:
            self.status_message.set(
                f"Success — cleaned all locations and returned to charging station."
            )
        else:
            self.status_message.set("Navigation failure — an target is unreachable.")

        for index, leg in enumerate(legs, start=1):
            self.leg_table.insert(
                "", "end",
                values=(index, leg["from"], leg["to"], leg["cost"], leg["nodes"]),
            )

        self._add_metric("Algorithm", self.algorithm.get())
        self._add_metric("Status", "Success" if success else "Navigation failure")
        self._add_metric("Schedule Valid", str(verify_schedule(assignment, constraints)))
        self._add_metric("Total Movement Cost", str(total_cost))
        self._add_metric("Total Nodes Expanded", str(total_nodes))

        self.draw_grid(
            grid,
            charging_station,
            dirty_locations,
            complete_path if success else None,
        )

    def _clear_tables(self):
        for table in (self.metrics, self.schedule_table, self.leg_table):
            for item in table.get_children():
                table.delete(item)

    def _add_metric(self, metric, value):
        self.metrics.insert("", "end", values=(metric, value))

    def draw_grid(self, grid, charging_station, dirty_locations, path=None):
        canvas = self.canvas
        canvas.delete("all")
        canvas.update_idletasks()
        width = max(canvas.winfo_width(), 400)
        height = max(canvas.winfo_height(), 400)
        rows = grid["rows"]
        cols = grid["cols"]
        cell = min(width / cols, height / rows)
        offset_x = (width - cols * cell) / 2
        offset_y = (height - rows * cell) / 2
        path_set = set(path or [])

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
                    fill = "#57b7a6"
                elif position in dirty_locations:
                    fill = "#f4c95d"
                elif position in path_set:
                    fill = "#9fd3c7"
                canvas.create_rectangle(
                    x1, y1, x2, y2, fill=fill, outline="#d9dee8"
                )

                if position == charging_station:
                    text = "C"
                elif position in dirty_locations:
                    text = f"D{dirty_locations.index(position) + 1}"
                else:
                    text = ""
                if text:
                    canvas.create_text(
                        (x1 + x2) / 2,
                        (y1 + y2) / 2,
                        text=text,
                        fill="#17223b",
                        font=("Segoe UI", max(8, int(cell / 4)), "bold"),
                    )


if __name__ == "__main__":
    app = VacuumRobotGUI()
    app.mainloop()
