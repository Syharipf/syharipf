"""Render profile data as animated test-run reports (SVG).

contributions: every day is a test case. A day with contributions passes, an empty day is skipped.
  A scanner sweeps the grid like a test runner, then the summary appears.
anime: every MyAnimeList entry is a test case. Completed passes, watching runs, on hold is skipped,
  dropped fails, plan to watch is todo.

Usage:
  GITHUB_TOKEN=... GITHUB_USER=syharipf MAL_USER=Syharipf python3 scripts/test_report.py OUT_DIR
  python3 scripts/test_report.py --check    # run the self-check
"""
import base64
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
                 pending="#161b22", skipped="#21262d", scanner="#58a6ff", ok="#3fb950", warn="#d29922", fail="#f85149",
                 run="#d29922", badge_text="#0d1117",
                 levels=["#0e4429", "#006d32", "#26a641", "#39d353"]),
    "light": dict(bg="#ffffff", border="#d0d7de", bar="#f6f8fa", text="#1f2328", muted="#656d76",
                  pending="#f6f8fa", skipped="#ebedf0", scanner="#0969da", ok="#1a7f37", warn="#9a6700", fail="#cf222e",
                  run="#bf8700", badge_text="#ffffff",
                  levels=["#9be9a8", "#40c463", "#30a14e", "#216e39"]),
}

CELL, GAP = 11, 3
STEP = CELL + GAP
X0, Y0 = 52, 112          # grid origin
DUR = "10s"               # one full test run, looped
SCAN_END = 0.7            # fraction of DUR spent scanning
WIDTH = X0 + 53 * STEP - GAP + 20  # a full year of weeks; the anime card matches it
FONT = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"
UA = {"User-Agent": "Mozilla/5.0 (profile test-report)"}

# MAL status code -> (test outcome, theme color, MAL label)
MAL_STATUS = {2: ("passed", "ok", "completed"), 1: ("running", "scanner", "watching"),
              3: ("skipped", "warn", "on hold"), 4: ("failed", "fail", "dropped"),
              6: ("todo", "muted", "plan to watch")}


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


def fetch_anime(user):
    """Whole public anime list from MAL's list JSON (300 entries per page)."""
    items = []
    while True:
        url = f"https://myanimelist.net/animelist/{user}/load.json?status=7&offset={len(items)}"
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as resp:
            page = json.load(resp)
        items += page
        if len(page) < 300:
            return items


def cover(url):
    """Covers must be embedded: GitHub serves SVGs as images, which cannot load external files."""
    url = url.split("?")[0].replace("/r/192x272/", "/r/96x136/")
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as resp:
        return "data:image/jpeg;base64," + base64.b64encode(resp.read()).decode()


def recent(items, n=3):
    return sorted(items, key=lambda a: a.get("updated_at") or 0, reverse=True)[:n]


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


def frame(width, height, title, command, t):
    """Terminal window: background, title bar, and the command line."""
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'viewBox="0 0 {width} {height}" font-family="{FONT}" font-size="12">',
           f'<title>{html.escape(title)}</title>',
           f'<rect x=".5" y=".5" width="{width - 1}" height="{height - 1}" rx="8" fill="{t["bg"]}" stroke="{t["border"]}"/>',
           f'<path d="M.5 32.5V8.5a8 8 0 0 1 8-8h{width - 17}a8 8 0 0 1 8 8v24z" fill="{t["bar"]}" stroke="{t["border"]}"/>']
    for i, c in enumerate(["#ff5f56", "#ffbd2e", "#27c93f"]):
        out.append(f'<circle cx="{20 + i * 16}" cy="16.5" r="5" fill="{c}"/>')
    out.append(f'<text x="{width / 2}" y="21" text-anchor="middle" fill="{t["muted"]}">{html.escape(title)}</text>')
    out.append(f'<text x="20" y="58" fill="{t["text"]}"><tspan fill="{t["ok"]}">$</tspan> {html.escape(command)}</text>')
    return out


def fade_in(delay, dur=0.5):
    """One-shot fade that keeps the element hidden until `delay` seconds."""
    total = delay + dur
    return (f'<animate attributeName="opacity" values="0;0;1" keyTimes="0;{delay / total:.3f};1" '
            f'dur="{total}s" fill="freeze"/>')


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

    out = frame(width, height, f"contributions.spec.ts — {login}",
                "qa run --suite contributions --since 365d", t)

    # RUNS/PASS status line
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


def render_anime(user, items, covers, t):
    width, height = WIDTH, 382
    counts = {code: sum(1 for a in items if a["status"] == code) for code in MAL_STATUS}
    failed = counts[4]
    out = frame(width, height, f"anime.spec.ts — {user} @ MyAnimeList",
                "qa run --suite anime --source myanimelist", t)
    badge, color = ("FAIL", t["fail"]) if failed else ("PASS", t["ok"])
    out.append(f'<rect x="20" y="68" width="44" height="18" rx="3" fill="{color}"/>'
               f'<text x="42" y="81" text-anchor="middle" font-weight="700" fill="{t["badge_text"]}">{badge}</text>'
               f'<text x="74" y="81" fill="{t["text"]}">profile/<tspan font-weight="700">anime.spec.ts</tspan></text>')
    out.append(f'<text x="20" y="112" font-size="10" fill="{t["muted"]}">RECENTLY UPDATED</text>')

    # one card per recently updated entry
    card_w = (width - 40 - 32) // 3
    for i, (a, img) in enumerate(zip(recent(items), covers)):
        x, y = 20 + i * (card_w + 16), 122
        title = a["anime_title_eng"] or a["anime_title"]
        short = title if len(title) <= 22 else title[:21] + "…"
        outcome, key, label = MAL_STATUS.get(a["status"], ("todo", "muted", "?"))
        mark = {"passed": "✓", "running": "▶", "skipped": "○", "failed": "✗"}.get(outcome, "·")
        seen, total = a["num_watched_episodes"], a["anime_num_episodes"]
        bar = 136 * min(seen / total, 1) if total else 0
        score = f'★ {a["score"]}' if a["score"] else "★ –"
        tx = x + 77
        out.append(f'<g>{fade_in(0.2 + 0.25 * i)}<title>{html.escape(title)}</title>'
                   f'<rect x="{x}" y="{y}" width="{card_w}" height="100" rx="6" fill="{t["bar"]}" stroke="{t["border"]}"/>'
                   f'<image href="{img}" x="{x + 10}" y="{y + 10}" width="57" height="80" preserveAspectRatio="xMidYMid slice"/>'
                   f'<text x="{tx}" y="{y + 26}" font-weight="700" fill="{t["text"]}">{html.escape(short)}</text>'
                   f'<text x="{tx}" y="{y + 46}" fill="{t[key]}">{mark} {label}</text>'
                   f'<rect x="{tx}" y="{y + 56}" width="136" height="6" rx="3" fill="{t["skipped"]}"/>'
                   f'<rect x="{tx}" y="{y + 56}" width="{bar:.1f}" height="6" rx="3" fill="{t[key]}"/>'
                   f'<text x="{tx}" y="{y + 82}" fill="{t["muted"]}">ep {seen}/{total or "?"}  {score}</text></g>')

    # stacked outcome bar, grows once
    bar_w, y = width - 40, 246
    out.append(f'<clipPath id="grow"><rect x="20" y="{y}" width="{bar_w}" height="10" rx="5">'
               f'<animate attributeName="width" values="0;0;{bar_w}" keyTimes="0;0.5;1" dur="2s" fill="freeze"/>'
               f'</rect></clipPath><g clip-path="url(#grow)"><rect x="20" y="{y}" width="{bar_w}" height="10" fill="{t["skipped"]}"/>')
    x = 20
    for code, (_, key, _) in MAL_STATUS.items():
        w = bar_w * counts[code] / len(items) if items else 0
        out.append(f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="10" fill="{t[key]}"/>')
        x += w
    out.append("</g>")
    legend = " · ".join(f"{o} = {label}" for o, _, label in MAL_STATUS.values())
    out.append(f'<text x="20" y="{y + 26}" font-size="10" fill="{t["muted"]}">{legend}</text>')

    # jest-style summary
    scores = [a["score"] for a in items if a["score"]]
    tests = ", ".join(f'<tspan fill="{t[key]}"{" font-weight=\"700\"" if code == 2 else ""}>{counts[code]} {o}</tspan>'
                      for code, (o, key, _) in MAL_STATUS.items())
    rows = [
        ("Tests:", f"{tests}, {len(items)} total"),
        ("Assertions:", f'{sum(a["num_watched_episodes"] for a in items):,} episodes watched'),
        ("Mean score:", f"{sum(scores) / len(scores):.2f} / 10 ({len(scores)} rated)" if scores else "–"),
        ("Ran at:", f'<tspan fill="{t["muted"]}">{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC</tspan>'),
    ]
    out.append(f"<g>{fade_in(1.2)}")
    for i, (label, value) in enumerate(rows):
        out.append(f'<text x="20" y="{y + 56 + i * 19}" fill="{t["text"]}"><tspan font-weight="700">{label}</tspan>'
                   f'<tspan x="120">{value}</tspan></text>')
    out.append("</g></svg>")
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
    anime = [{"status": st, "score": sc, "num_watched_episodes": 3, "anime_num_episodes": 0,
              "anime_title": "A" * 30, "anime_title_eng": "", "updated_at": i}
             for i, (st, sc) in enumerate([(2, 8), (2, 6), (1, 0), (4, 3)])]
    assert [a["updated_at"] for a in recent(anime)] == [3, 2, 1]
    svg = render_anime("demo", anime, ["data:,"] * 3, THEMES["light"])
    assert "2 passed" in svg and "1 failed" in svg and ">FAIL<" in svg and "5.67 / 10" in svg
    print("ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        check()
        sys.exit()
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "dist"
    login, mal_user = os.environ["GITHUB_USER"], os.environ["MAL_USER"]
    weeks = fetch(login, os.environ["GITHUB_TOKEN"])
    anime = fetch_anime(mal_user)
    covers = [cover(a["anime_image_path"]) for a in recent(anime)]
    os.makedirs(out_dir, exist_ok=True)
    for name, theme in THEMES.items():
        with open(os.path.join(out_dir, f"test-report-{name}.svg"), "w") as f:
            f.write(render(login, weeks, theme))
        with open(os.path.join(out_dir, f"anime-{name}.svg"), "w") as f:
            f.write(render_anime(mal_user, anime, covers, theme))
