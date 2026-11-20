# Intelligent Vacuum-Cleaning Robot GUI

Run the desktop interface:

```powershell
cd C:\Users\SAIF\Desktop\ai_cep
py app.py
```

Choose a preset from the **Scenario** menu, select `A*` or `BFS`, then click **Run simulation**. The map, cleaning order, navigation legs, movement cost, and nodes expanded appear in the results view.

Bundled scenarios:

- **Experiment** — `Environment.txt` and `Schedule.txt`
- **Sample** — `Environment_Sample.txt` and `Schedule_Sample.txt`
- **CSP Failure** — cyclic precedence constraints
- **Navigation Failure** — unreachable dirty location

To use files from your PC, choose **Custom files** and click **Browse PC...** for the environment and schedule files. You can also browse to a different file while any preset is selected.
