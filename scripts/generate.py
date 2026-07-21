#!/usr/bin/env python3
"""Generate terminal-style animated SVGs for the GitHub profile README.

Outputs:
  assets/contributions.svg  - contribution calendar drawn column-by-column
  assets/neofetch.svg       - dithered ASCII avatar + neofetch-style info panel

Data sources:
  * Contributions  -> GitHub GraphQL (token from GITHUB_TOKEN / GH_TOKEN), with
                      an unauthenticated HTML-scrape fallback.
  * Avatar         -> https://github.com/<user>.png

Animation is pure SMIL + CSS (no JavaScript) so it renders as an <img> on
github.com. Run:  python scripts/generate.py
"""
import io
import json
import os
import re
from datetime import date, datetime, timedelta, timezone

import requests
from PIL import Image, ImageFilter, ImageOps

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config.json")
ASSETS = os.path.join(ROOT, "assets")

MONO = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
LEVEL_MAP = {"NONE": 0, "FIRST_QUARTILE": 1, "SECOND_QUARTILE": 2,
             "THIRD_QUARTILE": 3, "FOURTH_QUARTILE": 4}


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# --------------------------------------------------------------------- fetching
def fetch_contributions_graphql(user, token):
    query = ("query($login:String!){user(login:$login){contributionsCollection{"
             "contributionCalendar{totalContributions weeks{contributionDays{"
             "date weekday contributionCount contributionLevel}}}}}}")
    r = requests.post(
        "https://api.github.com/graphql",
        json={"query": query, "variables": {"login": user}},
        headers={"Authorization": f"bearer {token}"},
        timeout=30,
    )
    r.raise_for_status()
    payload = r.json()
    if payload.get("errors"):
        raise RuntimeError(payload["errors"])
    cal = payload["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    columns = []
    for week in cal["weeks"]:
        col = {}
        for d in week["contributionDays"]:
            col[d["weekday"]] = {
                "date": d["date"],
                "count": d["contributionCount"],
                "level": LEVEL_MAP.get(d["contributionLevel"], 0),
            }
        columns.append(col)
    return columns, cal["totalContributions"]


def fetch_contributions_scrape(user):
    """Unauthenticated fallback: reconstruct the grid from the public fragment."""
    url = f"https://github.com/users/{user}/contributions"
    r = requests.get(
        url, timeout=30,
        headers={"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest"},
    )
    r.raise_for_status()
    html = r.text
    cells = re.findall(r'data-date="(\d{4}-\d{2}-\d{2})"[^>]*?data-level="(\d)"', html)
    if not cells:
        swapped = re.findall(r'data-level="(\d)"[^>]*?data-date="(\d{4}-\d{2}-\d{2})"', html)
        cells = [(d, lvl) for lvl, d in swapped]
    levels = {d: int(lvl) for d, lvl in cells}
    if not levels:
        raise RuntimeError("scrape found no contribution cells")
    dates = sorted(levels)
    first = date.fromisoformat(dates[0])
    first -= timedelta(days=(first.weekday() + 1) % 7)  # back to a Sunday
    last = date.fromisoformat(dates[-1])
    ncols = (last - first).days // 7 + 1
    columns = [dict() for _ in range(ncols)]
    for dstr, lvl in levels.items():
        dt = date.fromisoformat(dstr)
        ci = (dt - first).days // 7
        wd = (dt.weekday() + 1) % 7  # Python Mon=0 -> GitHub Sun=0
        if 0 <= ci < ncols:
            columns[ci][wd] = {"date": dstr, "count": 0, "level": lvl}
    m = re.search(r"([\d,]+)\s+contributions? in the last year", html)
    total = int(m.group(1).replace(",", "")) if m else sum(levels.values())
    return columns, total


def get_contributions(user, token):
    if token:
        try:
            return fetch_contributions_graphql(user, token)
        except Exception as e:  # noqa: BLE001
            print("[warn] GraphQL failed, falling back to scrape:", e)
    return fetch_contributions_scrape(user)


def fetch_avatar(user):
    r = requests.get(f"https://github.com/{user}.png", timeout=30,
                     headers={"User-Agent": "profile-bot"})
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGB")


# ------------------------------------------------------------------------ ascii
CHAR_ASPECT = 0.52   # rendered cell width / height, keeps the face un-stretched

# (threshold, glyph, colour) ramp from darkest -> brightest: dark-blue shadows
# through light blue to near-white highlights; empty space for the background.
RAMP = [
    (0.00, " ", None),
    (0.10, ".", "#12335a"),
    (0.20, ":", "#1c4e86"),
    (0.30, "-", "#1f6feb"),
    (0.42, "=", "#3b82f6"),
    (0.54, "+", "#58a6ff"),
    (0.66, "*", "#79c0ff"),
    (0.78, "#", "#a5d6ff"),
    (0.88, "%", "#cfe8ff"),
    (0.95, "@", "#eaf4ff"),
]


def _bucket(v):
    chosen = RAMP[0]
    for b in RAMP:
        if v >= b[0]:
            chosen = b
    return chosen


def avatar_to_ascii(img, cols=60):
    """Grayscale -> autocontrast + unsharp -> gamma -> Floyd-Steinberg dither.

    The sharpen recovers curly-hair texture and facial edges that a plain
    downscale smears; the gamma sinks the black hoodie/background to empty space
    while keeping the darker hair mass legible.
    """
    w, h = img.size
    rows = max(1, round(cols * (h / w) * CHAR_ASPECT))
    g = img.convert("L")
    g = ImageOps.autocontrast(g, cutoff=1)               # robust black -> white range
    g = g.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=2))
    small = g.resize((cols, rows), Image.LANCZOS)
    data = [p / 255.0 for p in small.getdata()]
    data = [v ** 1.3 for v in data]                      # gamma: sink bg, keep hair
    data = [0.0 if v < 0.09 else v for v in data]        # clean the near-black background
    buf = [[data[r * cols + c] for c in range(cols)] for r in range(rows)]
    out = []
    for r in range(rows):
        line = []
        for c in range(cols):
            v = min(1.0, max(0.0, buf[r][c]))
            thresh, glyph, colour = _bucket(v)
            line.append((glyph, colour))
            err = v - thresh                             # diffuse quant error
            if c + 1 < cols:
                buf[r][c + 1] += err * 7 / 16
            if r + 1 < rows:
                if c - 1 >= 0:
                    buf[r + 1][c - 1] += err * 3 / 16
                buf[r + 1][c] += err * 5 / 16
                if c + 1 < cols:
                    buf[r + 1][c + 1] += err * 1 / 16
        out.append(line)
    return out, cols, rows


# -------------------------------------------------------------------- timezone
def _last_sunday(year, month):
    d = date(year, 12, 31) if month == 12 else date(year, month + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - 6) % 7)


def rome_utc_offset(now_utc):
    """CET/CEST without tzdata: EU DST = last Sun Mar 01:00 UTC .. last Sun Oct."""
    y = now_utc.year
    start = datetime(y, 3, _last_sunday(y, 3).day, 1, tzinfo=timezone.utc)
    end = datetime(y, 10, _last_sunday(y, 10).day, 1, tzinfo=timezone.utc)
    return 2 if start <= now_utc < end else 1


# ------------------------------------------------------------- calendar svg
def build_calendar_svg(columns, total, cfg):
    th = cfg["theme"]
    bg, fg, dim, green = th["bg"], th["fg"], th["dim"], th["green"]
    blue, levels = th["prompt_path"], th["levels"]
    user = cfg["username"]
    cell, gap = 11, 3
    pitch = cell + gap
    ncols = len(columns)
    pad = 22
    prompt_y = pad + 18
    month_y = prompt_y + 24
    grid_top = month_y + 8
    label_w = 30
    grid_left = pad + label_w
    grid_w = ncols * pitch - gap
    grid_h = 7 * pitch - gap
    footer_y = grid_top + grid_h + 26
    width = grid_left + grid_w + pad
    height = footer_y + 12
    step = 42                                            # ms between columns
    sweep = round(ncols * step / 1000.0, 2)

    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
         f'viewBox="0 0 {width} {height}" font-family="{MONO}">']
    # `backwards` fill keeps cells hidden only DURING their reveal window; the
    # resting state is fully visible, so non-animating renderers still show the
    # complete calendar instead of a blank grid.
    p.append('<style>.c{animation:cf .2s ease-out backwards}'
             '@keyframes cf{from{opacity:0}to{opacity:1}}'
             '.fade{animation:cf .6s ease backwards}'
             '.cur{animation:bl 1s steps(1) infinite}@keyframes bl{50%{opacity:0}}</style>')
    p.append(f'<rect width="{width}" height="{height}" rx="8" fill="{bg}"/>')
    # comet-trail gradient painted just behind the leading scan line
    p.append(f'<defs><linearGradient id="trail" x1="0" y1="0" x2="1" y2="0">'
             f'<stop offset="0" stop-color="{green}" stop-opacity="0"/>'
             f'<stop offset="1" stop-color="{green}" stop-opacity="0.45"/></linearGradient></defs>')

    prompt = f"{user}@github ~ $ ./contributions.sh"
    p.append(f'<text x="{pad}" y="{prompt_y}" font-size="14">'
             f'<tspan fill="{green}">{user}@github</tspan>'
             f'<tspan fill="{dim}"> </tspan><tspan fill="{blue}">~</tspan>'
             f'<tspan fill="{dim}"> $ </tspan>'
             f'<tspan fill="{fg}">./contributions.sh</tspan></text>')
    curx = pad + len(prompt) * 14 * 0.6 + 4
    p.append(f'<rect class="cur" x="{curx:.0f}" y="{prompt_y - 11}" '
             f'width="7" height="14" fill="{green}"/>')

    prev = None
    for ci, col in enumerate(columns):
        day = next((col[wd] for wd in range(7) if wd in col), None)
        if not day:
            continue
        m = int(day["date"][5:7])
        if m != prev:
            p.append(f'<text x="{grid_left + ci * pitch}" y="{month_y}" '
                     f'font-size="10" fill="{dim}">{MONTHS[m - 1]}</text>')
            prev = m

    for wd, lab in [(1, "Mon"), (3, "Wed"), (5, "Fri")]:
        p.append(f'<text x="{pad}" y="{grid_top + wd * pitch + cell - 1}" '
                 f'font-size="9" fill="{dim}">{lab}</text>')

    for ci, col in enumerate(columns):
        x = grid_left + ci * pitch
        delay = ci * step
        for wd in range(7):
            if wd not in col:
                continue
            d = col[wd]
            y = grid_top + wd * pitch
            p.append(f'<rect class="c" style="animation-delay:{delay}ms" x="{x}" y="{y}" '
                     f'width="{cell}" height="{cell}" rx="2" fill="{levels[d["level"]]}">'
                     f'<title>{d["count"]} on {d["date"]}</title></rect>')

    # trailing green glow (cells fade in within it) ...
    trail_w = 46
    p.append(f'<rect x="{grid_left - trail_w}" y="{grid_top}" width="{trail_w}" height="{grid_h}" '
             f'fill="url(#trail)" opacity="0">'
             f'<animate attributeName="x" from="{grid_left - trail_w}" '
             f'to="{grid_left + grid_w - trail_w}" dur="{sweep}s" fill="freeze"/>'
             f'<animate attributeName="opacity" values="0;0.85;0.85;0" keyTimes="0;0.06;0.9;1" '
             f'dur="{sweep}s" fill="freeze"/></rect>')
    # ... and the bright leading line itself
    p.append(f'<rect x="{grid_left}" y="{grid_top - 2}" width="3" height="{grid_h + 4}" '
             f'fill="{green}" opacity="0">'
             f'<animate attributeName="x" from="{grid_left}" to="{grid_left + grid_w}" '
             f'dur="{sweep}s" fill="freeze"/>'
             f'<animate attributeName="opacity" values="0.95;0.95;0" keyTimes="0;0.92;1" '
             f'dur="{sweep}s" fill="freeze"/></rect>')

    p.append(f'<text class="fade" style="animation-delay:{sweep + 0.2:.2f}s" '
             f'x="{grid_left}" y="{footer_y}" font-size="13" fill="{fg}">'
             f'<tspan fill="{green}" font-weight="700">{total}</tspan> '
             f'contributions in the last year</text>')
    p.append("</svg>")
    return "".join(p)


# ------------------------------------------------------------- neofetch svg
def build_neofetch_svg(art, cols, rows, cfg, utc_label):
    th = cfg["theme"]
    bg, dim = th["bg"], th["dim"]
    blue = th["prompt_path"]
    user = cfg["username"]
    # block 2 uses a white / light-blue palette (distinct from the green calendar).
    # `green` here is the light-blue accent; reusing the name keeps the diff small.
    green = "#79c0ff"   # accent: keys, section headers, prompt, scan line
    fg = "#d6e4f5"      # near-white values
    white = "#eaf4ff"   # brightest highlight (name)

    pad = 22
    prompt_y = pad + 18
    art_fs = 12.0
    art_ch = art_fs * 0.60
    art_lh = art_fs * 1.15
    art_left = pad
    art_top = prompt_y + 16
    art_w = cols * art_ch
    art_h = rows * art_lh

    info_fs = 13.0
    info_ch = info_fs * 0.60
    info_lh = 19.0
    info_left = art_left + art_w + 36
    info_top = art_top + info_fs

    st = cfg["stack"]

    def stack_line(label, items):
        return [("  " + f"{label:<8}", green, False),
                (" " + " · ".join(items), fg, False)]

    lines = [
        [(user, green, True), ("@", dim, False), ("github", green, True)],
        [("—" * 22, dim, False)],
        [(f"{'Name':<10}", green, True), (cfg["name"], white, False)],
        [(f"{'Role':<10}", green, True), (cfg["role"], fg, False)],
        [(f"{'Edu':<10}", green, True), (cfg["education"], fg, False)],
        [(f"{'Location':<10}", green, True), (f'{cfg["location"]} · {utc_label}', fg, False)],
        [(f"{'Email':<10}", green, True), (cfg["email"], fg, False)],
        [(f"{'Website':<10}", green, True), (cfg["website"], fg, False)],
        [("", fg, False)],
        [("Stack", green, True)],
    ]
    for k in ["Frontend", "Backend", "AI-ML", "Cloud"]:
        if k in st:
            lines.append(stack_line(k, st[k]))
    lines.append([("", fg, False)])
    lines.append([("Highlights", green, True)])
    for hl in cfg["highlights"]:
        lines.append([("  ▸ ", green, False), (hl, fg, False)])

    n = len(lines)
    max_chars = max(sum(len(t) for t, *_ in segs) for segs in lines)
    info_w = max_chars * info_ch
    width = int(info_left + info_w + pad)
    height = int(max(art_top + art_h, info_top + n * info_lh + 6) + pad)

    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
         f'viewBox="0 0 {width} {height}" font-family="{MONO}">']
    p.append('<style>.ln{animation:cf .45s ease backwards}'
             '@keyframes cf{from{opacity:0}to{opacity:1}}'
             '.ch{animation:tp .01s steps(1,end) backwards}'
             '@keyframes tp{from{opacity:0}to{opacity:1}}'
             '.cur{animation:bl 1s steps(1) infinite}@keyframes bl{50%{opacity:0}}</style>')
    p.append(f'<rect width="{width}" height="{height}" rx="8" fill="{bg}"/>')

    prompt = f"{user}@github ~ $ whoami"
    p.append(f'<text x="{pad}" y="{prompt_y}" font-size="14">'
             f'<tspan fill="{green}">{user}@github</tspan>'
             f'<tspan fill="{dim}"> </tspan><tspan fill="{blue}">~</tspan>'
             f'<tspan fill="{dim}"> $ </tspan><tspan fill="{fg}">whoami</tspan></text>')
    curx = pad + len(prompt) * 14 * 0.6 + 4
    p.append(f'<rect class="cur" x="{curx:.0f}" y="{prompt_y - 11}" '
             f'width="7" height="14" fill="{green}"/>')

    # --- ASCII art: one <text> per colour-run, revealed left-to-right by column
    #     so the portrait "draws" with the same scanline as the calendar. Runs
    #     are positioned by absolute x (with textLength) so columns stay aligned
    #     regardless of the viewer's monospace metrics. Background cells are not
    #     drawn; the resting state is visible, so it degrades gracefully.
    art_step = 30  # ms per column
    for r, row in enumerate(art):
        y = art_top + (r + 1) * art_lh
        cur, buf, start = "___", "", 0
        runs = []
        for c, (ch, colour) in enumerate(row):
            key = colour or ""
            if key == cur:
                buf += ch
            else:
                if buf and cur:
                    runs.append((buf, cur, start))
                buf, cur, start = ch, key, c
        if buf and cur:
            runs.append((buf, cur, start))
        for text, colour, start_col in runs:
            x = art_left + start_col * art_ch
            p.append(f'<text class="ln" style="animation-delay:{start_col * art_step}ms" '
                     f'x="{x:.1f}" y="{y:.1f}" font-size="{art_fs:.0f}" fill="{colour}" '
                     f'textLength="{len(text) * art_ch:.1f}" lengthAdjust="spacingAndGlyphs" '
                     f'xml:space="preserve">{esc(text)}</text>')

    # --- green scan bar sweeping left-to-right across the portrait
    sweep = round(cols * art_step / 1000.0, 2)
    art_right = art_left + art_w
    p.append(f'<rect x="{art_left}" y="{art_top:.1f}" width="2" height="{art_h:.1f}" '
             f'fill="{green}" opacity="0">'
             f'<animate attributeName="x" from="{art_left}" to="{art_right:.1f}" '
             f'dur="{sweep}s" fill="freeze"/>'
             f'<animate attributeName="opacity" values="0.9;0.9;0" keyTimes="0;0.92;1" '
             f'dur="{sweep}s" fill="freeze"/></rect>')

    # --- info panel: military teletype — each character hard-cuts in on one
    #     global timer (no fade). Base state is visible -> degrades gracefully.
    total_chars = sum(len(t) for segs in lines for t, *_ in segs) or 1
    cstep = max(3, min(14, round(2800 / total_chars)))   # aim for ~2.8s total
    cbase, char_i = 250, 0
    for i, segs in enumerate(lines):
        y = info_top + i * info_lh
        tsp = ""
        for text, colour, bold in segs:
            weight = ' font-weight="700"' if bold else ""
            for ch in text:
                tsp += (f'<tspan class="ch" style="animation-delay:{cbase + char_i * cstep}ms" '
                        f'fill="{colour}"{weight}>{esc(ch)}</tspan>')
                char_i += 1
        p.append(f'<text x="{info_left:.1f}" y="{y:.1f}" '
                 f'font-size="{info_fs:.0f}" xml:space="preserve">{tsp}</text>')

    p.append("</svg>")
    return "".join(p)


# ------------------------------------------------------------------------ main
def main():
    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    user = os.environ.get("GH_USERNAME") or cfg["username"]
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

    columns, total = get_contributions(user, token)
    art, cols, rows = avatar_to_ascii(fetch_avatar(user), cfg.get("ascii_cols", 46))
    utc = f"UTC+{rome_utc_offset(datetime.now(timezone.utc))}"

    os.makedirs(ASSETS, exist_ok=True)
    write(os.path.join(ASSETS, "contributions.svg"), build_calendar_svg(columns, total, cfg))
    write(os.path.join(ASSETS, "neofetch.svg"), build_neofetch_svg(art, cols, rows, cfg, utc))
    print(f"ok: total={total} weeks={len(columns)} ascii={cols}x{rows} tz={utc}")


if __name__ == "__main__":
    main()
