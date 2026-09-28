"""Render the GitHub contribution calendar as an animated test-run report (SVG).

Every day is a test case: a day with contributions passes, an empty day is skipped.
A scanner sweeps the grid like a test runner, then the summary appears.

Usage:
  GITHUB_TOKEN=... GITHUB_USER=syharipf python3 scripts/test_report.py OUT_DIR
  python3 scripts/test_report.py --check    # run the self-check
"""
import html
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

QUERY = """query($login: String!) { user(login: $login) { contributionsCollection {
  contributionCalendar { totalContributions weeks { contributionDays {
    date contributionCount contributionLevel } } } } } }"""

LEVELS = ["FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE"]

THEMES = {
    "dark": dict(bg="#0d1117", border="#30363d", bar="#161b22", text="#c9d1d9", muted="#8b949e",
                 pending="#161b22", skipped="#21262d", scanner="#58a6ff", ok="#3fb950", warn="#d29922",
                 run="#d29922", badge_text="#0d1117",
                 levels=["#0e4429", "#006d32", "#26a641", "#39d353"]),
    "light": dict(bg="#ffffff", border="#d0d7de", bar="#f6f8fa", text="#1f2328", muted="#656d76",
                  pending="#f6f8fa", skipped="#ebedf0", scanner="#0969da", ok="#1a7f37", warn="#9a6700",
                  run="#bf8700", badge_text="#ffffff",
                  levels=["#9be9a8", "#40c463", "#30a14e", "#216e39"]),
}

CELL, GAP = 11, 3
STEP = CELL + GAP
X0, Y0 = 52, 112          # grid origin
DUR = "10s"               # one full test run, looped
SCAN_END = 0.7            # fraction of DUR spent scanning
FONT = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"


def fetch(login, token):
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": {"login": login}}).encode(),
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.load(resp)
    if "errors" in body:
        sys.exit(f"GraphQL error: {body['errors']}")
    return body["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]


def streaks(counts):
    """(current, longest) runs of non-zero days. Today may still be empty without breaking the streak."""
    longest = run = 0
    for c in counts:
        run = run + 1 if c else 0
        longest = max(longest, run)
    tail = counts[:-1] if counts and not counts[-1] else counts
    current = 0
    for c in reversed(tail):
        if not c:
            break
        current += 1
    return current, longest


def render(login, weeks, t):
    days = [d for w in weeks for d in w["contributionDays"]]
    counts = [d["contributionCount"] for d in days]
    passed = sum(1 for c in counts if c)
    skipped = len(counts) - passed
    current, longest = streaks(counts)
    coverage = 100 * passed / len(counts) if counts else 0

    grid_w = len(weeks) * STEP - GAP
    grid_h = 7 * STEP - GAP
    width = X0 + grid_w + 20
    height = Y0 + grid_h + 128
    k = f"0;{SCAN_END};0.99;1"   # keyTimes shared by the scan animations

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'viewBox="0 0 {width} {height}" font-family="{FONT}" font-size="12">',
           f'<title>contributions.spec — {html.escape(login)}</title>',
           f'<rect x=".5" y=".5" width="{width - 1}" height="{height - 1}" rx="8" fill="{t["bg"]}" stroke="{t["border"]}"/>',
           f'<path d="M.5 32.5V8.5a8 8 0 0 1 8-8h{width - 17}a8 8 0 0 1 8 8v24z" fill="{t["bar"]}" stroke="{t["border"]}"/>']
    for i, c in enumerate(["#ff5f56", "#ffbd2e", "#27c93f"]):
        out.append(f'<circle cx="{20 + i * 16}" cy="16.5" r="5" fill="{c}"/>')
    out.append(f'<text x="{width / 2}" y="21" text-anchor="middle" fill="{t["muted"]}">'
               f'contributions.spec.ts — {html.escape(login)}</text>')

    # command + RUNS/PASS status line
    out.append(f'<text x="20" y="58" fill="{t["text"]}"><tspan fill="{t["ok"]}">$</tspan> '
               f'qa run --suite contributions --since 365d</text>')
    for label, color, values in [("RUNS", t["run"], "1;1;0;0;1"), ("PASS", t["ok"], "0;0;1;1;0")]:
        base = values.split(";")[2]  # static renderers show the finished state
        out.append(f'<g opacity="{base}"><rect x="20" y="68" width="44" height="18" rx="3" fill="{color}"/>'
                   f'<text x="42" y="81" text-anchor="middle" font-weight="700" fill="{t["badge_text"]}">{label}</text>'
                   f'<animate attributeName="opacity" values="{values}" keyTimes="0;{SCAN_END};{SCAN_END + 0.01};0.99;1" '
                   f'dur="{DUR}" repeatCount="indefinite"/></g>')
    out.append(f'<text x="74" y="81" fill="{t["text"]}">profile/<tspan font-weight="700">contributions.spec.ts</tspan></text>')

    # month + weekday labels
    last_col = -3
    prev_month = None
    for i, w in enumerate(weeks):
        month = w["contributionDays"][0]["date"][:7]
        if month != prev_month and i - last_col >= 3 and i < len(weeks) - 2:
            name = datetime.strptime(month, "%Y-%m").strftime("%b")
            out.append(f'<text x="{X0 + i * STEP}" y="{Y0 - 8}" font-size="10" fill="{t["muted"]}">{name}</text>')
            last_col = i
        prev_month = month
    for row, name in [(1, "Mon"), (3, "Wed"), (5, "Fri")]:
        out.append(f'<text x="20" y="{Y0 + row * STEP + 9}" font-size="10" fill="{t["muted"]}">{name}</text>')

    # pending layer, then the result layer revealed by the scanner
    cells_pending, cells_result = [], []
    for i, w in enumerate(weeks):
        for d in w["contributionDays"]:
            row = datetime.strptime(d["date"], "%Y-%m-%d").weekday()
            row = (row + 1) % 7  # GitHub rows start on Sunday
            x, y = X0 + i * STEP, Y0 + row * STEP
            lvl = d["contributionLevel"]
            fill = t["levels"][LEVELS.index(lvl)] if lvl in LEVELS else t["skipped"]
            tip = f'{d["date"]}: {d["contributionCount"]} contributions — {"passed" if d["contributionCount"] else "skipped"}'
            cells_pending.append(f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2" '
                                 f'fill="none" stroke="{t["border"]}"/>')
            cells_result.append(f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2" fill="{fill}">'
                                f'<title>{tip}</title></rect>')
    out.append(f'<clipPath id="reveal"><rect x="{X0 - 2}" y="{Y0 - 2}" width="{grid_w + 4}" height="{grid_h + 4}">'
               f'<animate attributeName="width" values="0;{grid_w + 4};{grid_w + 4};0" keyTimes="{k}" '
               f'dur="{DUR}" repeatCount="indefinite"/></rect></clipPath>')
    out.append("<g>" + "".join(cells_pending) + "</g>")
    out.append('<g clip-path="url(#reveal)">' + "".join(cells_result) + "</g>")
    out.append(f'<rect x="{X0 - 3}" y="{Y0 - 5}" width="2" height="{grid_h + 10}" rx="1" fill="{t["scanner"]}" opacity="0">'
               f'<animate attributeName="x" values="{X0 - 3};{X0 + grid_w + 1};{X0 + grid_w + 1};{X0 - 3}" '
               f'keyTimes="{k}" dur="{DUR}" repeatCount="indefinite"/>'
               f'<animate attributeName="opacity" values="1;1;0;0" keyTimes="0;{SCAN_END};{SCAN_END + 0.01};1" '
               f'dur="{DUR}" repeatCount="indefinite"/></rect>')

    # jest-style summary, shown once the scan finishes
    y = Y0 + grid_h + 32
    rows = [
        ("Tests:", f'<tspan fill="{t["ok"]}" font-weight="700">{passed} passed</tspan>, '
                   f'<tspan fill="{t["warn"]}">{skipped} skipped</tspan>, {len(counts)} total'),
        ("Assertions:", f'{sum(counts):,} contributions'),
        ("Streak:", f'{current} days current, {longest} days longest'),
        ("Coverage:", f'{coverage:.1f}% of days'),
        ("Ran at:", f'<tspan fill="{t["muted"]}">{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC</tspan>'),
    ]
    out.append('<g>')
    for i, (label, value) in enumerate(rows):
        out.append(f'<text x="20" y="{y + i * 19}" fill="{t["text"]}"><tspan font-weight="700">{label}</tspan>'
                   f'<tspan x="120">{value}</tspan></text>')
    out.append(f'<animate attributeName="opacity" values="0;0;1;1;0" keyTimes="0;{SCAN_END};{SCAN_END + 0.05};0.99;1" '
               f'dur="{DUR}" repeatCount="indefinite"/></g>')

    # legend
    lx = X0 + grid_w - 5 * STEP - 40  # leaves room for the "passed" label
    out.append(f'<text x="{lx - 6}" y="{y + 9 - 12}" text-anchor="end" font-size="10" fill="{t["muted"]}">skipped</text>')
    for i, c in enumerate([t["skipped"], *t["levels"]]):
        out.append(f'<rect x="{lx + i * STEP}" y="{y - 12}" width="{CELL}" height="{CELL}" rx="2" fill="{c}"/>')
    out.append(f'<text x="{lx + 5 * STEP + 3}" y="{y + 9 - 12}" font-size="10" fill="{t["muted"]}">passed</text>')

    out.append("</svg>")
    return "\n".join(out)


def check():
    assert streaks([]) == (0, 0)
    assert streaks([1, 1, 0, 1, 1, 1]) == (3, 3)
    assert streaks([1, 1, 1, 0, 2, 0]) == (1, 3)   # empty today keeps yesterday's streak
    assert streaks([1, 0, 0]) == (0, 1)
    weeks = [{"contributionDays": [{"date": f"2026-01-0{d}", "contributionCount": d % 2,
                                    "contributionLevel": "FIRST_QUARTILE" if d % 2 else "NONE"}
                                   for d in range(4, 8)]}]
    assert "2 passed" in render("demo", weeks, THEMES["dark"])
    print("ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        check()
        sys.exit()
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "dist"
    login = os.environ["GITHUB_USER"]
    weeks = fetch(login, os.environ["GITHUB_TOKEN"])
    os.makedirs(out_dir, exist_ok=True)
    for name, theme in THEMES.items():
        with open(os.path.join(out_dir, f"test-report-{name}.svg"), "w") as f:
            f.write(render(login, weeks, theme))
