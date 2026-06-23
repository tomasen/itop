#!/usr/bin/env python3
"""itop — a terminal network health monitor with a LOGARITHMIC latency graph.

One live graph, log y-axis (so sub-millisecond LAN latency and tens-of-ms
internet latency are both readable at once), smooth braille lines, and a legend
that shows each target's IP plus a friendly name.

Targets are configurable and can be:
  • icmp — a normal ping (host "auto" = your default gateway)
  • tcp  — a TCP-port connect (for hosts that block ICMP)
  • hop  — an *intermediate* router, measured by a TTL-limited probe toward a
           destination. Lets you watch a specific hop even on a load-balanced
           multi-WAN link, where you can't address that hop directly.

Config:  ~/.config/itop/config.json   (see config.example.json)
With no config it auto-discovers your gateway and connection(s) — works for a
single connection or a multi-WAN / load-balanced router (broken out per WAN).

Keys: q or Ctrl-C to quit.   Self-test: itop.py --selftest
"""
import argparse, json, math, os, re, socket, subprocess, sys, threading, time
from collections import deque

APP = "itop"

DEFAULT_CONFIG = {
    "interval_seconds": 1.0,
    "y_min_ms": 0.3,
    "y_max_ms": 400.0,
    "gridlines_ms": [0.5, 1, 2, 5, 10, 20, 50, 100, 200],
    "extra_targets": [],
}

# color name -> (xterm-256 index, basic-8 fallback). Dark/saturated tones that
# stay readable on a light OR dark terminal background.
PALETTE = {
    "gray":    (240, "white"),   "green": (28, "green"),  "olive": (64, "green"),
    "orange":  (130, "yellow"),  "brown": (94, "yellow"), "purple": (90, "magenta"),
    "magenta": (90, "magenta"),  "blue":  (26, "blue"),   "teal":  (30, "cyan"),
    "red":     (160, "red"),
}

# y-axis scale (filled in from config by apply_scale)
YMIN = YMAX = LYMIN = LYMAX = 0.0
GRIDLINES = []

def apply_scale(cfg):
    global YMIN, YMAX, LYMIN, LYMAX, GRIDLINES
    YMIN = float(cfg["y_min_ms"]); YMAX = float(cfg["y_max_ms"])
    LYMIN, LYMAX = math.log10(YMIN), math.log10(YMAX)
    GRIDLINES = [g for g in cfg["gridlines_ms"] if YMIN <= g <= YMAX]

def frac_of(v):
    v = min(max(v, YMIN), YMAX)
    return (math.log10(v) - LYMIN) / (LYMAX - LYMIN)   # 0=bottom .. 1=top

# ---- config --------------------------------------------------------------
def config_path(explicit=None):
    if explicit:
        return explicit
    if os.environ.get("ITOP_CONFIG"):
        return os.environ["ITOP_CONFIG"]
    return os.path.join(os.path.expanduser("~"), ".config", APP, "config.json")

def load_config(path):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))   # deep copy
    if path and os.path.exists(path):
        try:
            with open(path) as f:
                user = json.load(f)
            for k, v in user.items():
                if not k.startswith("_"):
                    cfg[k] = v
        except Exception as e:
            sys.stderr.write(f"{APP}: ignoring bad config {path}: {e}\n")
    return cfg

def default_gateway():
    try:  # macOS / BSD
        out = subprocess.run(["route", "-n", "get", "default"],
                             capture_output=True, text=True, timeout=3).stdout
        m = re.search(r"gateway:\s*([\d.]+)", out)
        if m:
            return m.group(1)
    except Exception:
        pass
    try:  # Linux
        out = subprocess.run(["ip", "route", "show", "default"],
                             capture_output=True, text=True, timeout=3).stdout
        m = re.search(r"default via ([\d.]+)", out)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None

# ---- auto-discovery ------------------------------------------------------
# public resolvers used only to map paths; more = better coverage of 3+ WANs
DISCOVERY_PROBES = ["1.1.1.1", "8.8.8.8", "9.9.9.9", "208.67.222.222", "1.0.0.1",
                    "8.8.4.4", "4.2.2.2", "64.6.64.6", "208.67.220.220", "149.112.112.112"]
# per-WAN color triples: (gateway, intermediate hop, internet)
WAN_COLORS = [("green", "olive", "purple"), ("orange", "brown", "blue"),
              ("teal", "teal", "magenta"), ("red", "brown", "gray")]

def trace_hop_ip(dest, ttl):
    """IP of the router `ttl` hops toward dest (None if it doesn't answer)."""
    try:
        out = subprocess.run(
            ["traceroute", "-f", str(ttl), "-m", str(ttl), "-q", "1", "-w", "2", dest],
            capture_output=True, text=True, timeout=6).stdout
        m = _PARENS_IP.search(out)
        return m.group(1) if m else None
    except Exception:
        return None

def discover_targets():
    """Probe several public IPs, group by their first WAN hop to find each WAN,
    and build router + per-WAN gateway/hop/internet lines automatically."""
    hop2 = {}
    lock = threading.Lock()
    def work(d):
        ip = trace_hop_ip(d, 2)
        with lock:
            hop2[d] = ip
    ths = [threading.Thread(target=work, args=(d,), daemon=True) for d in DISCOVERY_PROBES]
    for t in ths: t.start()
    for t in ths: t.join(timeout=7)

    wans, seen = [], set()           # one representative dest per distinct WAN gateway
    for d in DISCOVERY_PROBES:
        gw = hop2.get(d)
        if gw and gw not in seen:
            seen.add(gw); wans.append(d)

    targets = []
    gw = default_gateway()
    if gw:
        targets.append({"label": "router", "kind": "icmp", "host": gw, "color": "gray"})
    multi = len(wans) > 1
    for i, dest in enumerate(wans):
        p = f"WAN-{chr(ord('A') + i)} " if multi else ""
        c = WAN_COLORS[i % len(WAN_COLORS)]
        targets.append({"label": f"{p}gw",  "kind": "hop",  "host": dest, "ttl": 2, "color": c[0]})
        targets.append({"label": f"{p}hop", "kind": "hop",  "host": dest, "ttl": 3, "color": c[1]})
        targets.append({"label": f"{p}net", "kind": "icmp", "host": dest, "color": c[2]})
    if not wans:                      # offline / no traceroute: still show something
        targets.append({"label": "internet", "kind": "icmp", "host": "1.1.1.1", "color": "blue"})
    return targets

def resolve_targets(cfg):
    """Explicit 'targets' fully override; otherwise auto-discover and append any
    'extra_targets' from the config."""
    if cfg.get("targets"):
        return list(cfg["targets"])
    return discover_targets() + list(cfg.get("extra_targets", []))

# ---- data ----------------------------------------------------------------
HIST = 2000
class Series:
    def __init__(self, label, color, host=""):
        self.label, self.color, self.host = label, color, host
        self.vals = deque(maxlen=HIST)
        self.last = None
        self.lock = threading.Lock()
    def push(self, v):
        with self.lock:
            self.vals.append(v); self.last = v
    def snapshot(self, n):
        with self.lock:
            return list(self.vals)[-n:]

_TIME = re.compile(r"time[=<]([\d.]+)\s*ms")
_PARENS_IP = re.compile(r"\(([\d.]+)\)")

def icmp_worker(host, series, stop, interval):
    while not stop.is_set():
        try:
            p = subprocess.Popen(["ping", host], stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, text=True, bufsize=1)
        except OSError:
            stop.wait(2); continue
        try:
            for line in p.stdout:
                if stop.is_set():
                    break
                m = _TIME.search(line)
                if m:
                    series.push(float(m.group(1)))
                elif "timeout" in line.lower() or "100% packet loss" in line:
                    series.push(None)
        finally:
            p.terminate()
            try: p.wait(timeout=1)
            except Exception: pass

def tcp_worker(host, port, series, stop, interval):
    while not stop.is_set():
        t0 = time.perf_counter()
        try:
            socket.create_connection((host, port), timeout=2).close()
            series.push((time.perf_counter() - t0) * 1000.0)
        except OSError:
            series.push(None)
        stop.wait(interval)

def hop_worker(dest, ttl, series, stop, interval):
    # Measure an intermediate hop: send a probe toward `dest` with TTL=`ttl` so
    # it expires AT that hop, and time the "time exceeded" reply. Routing follows
    # the final dest, so on a multi-WAN link this reliably hits that WAN's hop.
    while not stop.is_set():
        try:
            out = subprocess.run(
                ["traceroute", "-f", str(ttl), "-m", str(ttl), "-q", "1", "-w", "2", dest],
                capture_output=True, text=True, timeout=6).stdout
            ip = _PARENS_IP.search(out)
            if ip:
                series.host = ip.group(1)
            m = _TIME.search(out) or re.search(r"([\d.]+)\s*ms", out)
            series.push(float(m.group(1)) if m else None)
        except Exception:
            series.push(None)
        stop.wait(interval)

def start_targets(targets, interval, stop):
    gw = {"v": None}
    def gateway():
        if gw["v"] is None:
            gw["v"] = default_gateway() or ""
        return gw["v"]
    series_list, threads = [], []
    for t in targets:
        kind = t.get("kind", "icmp")
        host = t.get("host", "")
        if host == "auto":
            host = gateway()
            if not host:
                sys.stderr.write(f"{APP}: could not detect default gateway; skipping '{t.get('label')}'\n")
                continue
        color = t.get("color", "gray")
        if kind == "tcp":
            disp = f"{host}:{t.get('port', 0)}"
        elif kind == "hop":
            disp = host  # replaced by the discovered hop IP once probed
        else:
            disp = host
        s = Series(t.get("label", host), color, disp)
        series_list.append(s)
        if kind == "icmp":
            th = threading.Thread(target=icmp_worker, args=(host, s, stop, interval), daemon=True)
        elif kind == "tcp":
            th = threading.Thread(target=tcp_worker, args=(host, int(t.get("port", 0)), s, stop, interval), daemon=True)
        elif kind == "hop":
            th = threading.Thread(target=hop_worker, args=(host, int(t.get("ttl", 2)), s, stop, interval), daemon=True)
        else:
            sys.stderr.write(f"{APP}: unknown kind '{kind}' for '{t.get('label')}'\n"); continue
        th.start(); threads.append(th)
    return series_list

# ---- braille line rendering ---------------------------------------------
BRAILLE_BITS = {(0, 0): 0x01, (0, 1): 0x02, (0, 2): 0x04, (0, 3): 0x40,
                (1, 0): 0x08, (1, 1): 0x10, (1, 2): 0x20, (1, 3): 0x80}
LM = 7   # left margin for y-axis labels

class Braille:
    def __init__(self, cols, rows):
        self.PW, self.PH = cols * 2, rows * 4
        self.bits = [[0] * cols for _ in range(rows)]
        self.col = [[None] * cols for _ in range(rows)]
    def plot(self, px, py, color):
        if 0 <= px < self.PW and 0 <= py < self.PH:
            cc, cr = px // 2, py // 4
            self.bits[cr][cc] |= BRAILLE_BITS[(px % 2, py % 4)]
            self.col[cr][cc] = color
    def line(self, x0, y0, x1, y1, color):
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            self.plot(x0, y0, color)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy: err += dy; x0 += sx
            if e2 <= dx: err += dx; y0 += sy

def graph_height(H, top):
    return max(1, (H - 2) - top)   # rows top..H-3 ; H-2 x-note, H-1 status

def build_graph_grid(series_list, W, H, top):
    gh = graph_height(H, top)
    cols = max(1, W - LM)
    cv = Braille(cols, gh)
    def ypix(v):
        return int(round((1 - frac_of(v)) * (cv.PH - 1)))
    for s in series_list:
        samples = s.snapshot(cv.PW)
        base = cv.PW - len(samples)
        prev = None
        for i, v in enumerate(samples):
            x = base + i
            if v is None:
                prev = None; continue
            y = ypix(v)
            if prev is not None:
                cv.line(prev[0], prev[1], x, y, s.color)
            else:
                cv.plot(x, y, s.color)
            prev = (x, y)
    gl = {int(round((1 - frac_of(g)) * (gh - 1))): g for g in GRIDLINES}
    cg = [[" "] * W for _ in range(gh)]
    colg = [[None] * W for _ in range(gh)]
    for r in range(gh):
        if r in gl:
            for i, ch in enumerate(f"{gl[r]:g}".rjust(LM - 1)):
                cg[r][i] = ch
            cg[r][LM - 1] = "┤"
        for cc in range(cols):
            c = LM + cc
            if cv.bits[r][cc]:
                cg[r][c] = chr(0x2800 + cv.bits[r][cc]); colg[r][c] = cv.col[r][cc]
            elif r in gl:
                cg[r][c] = "·"
    return cg, colg

def legend_segments(series_list):
    segs = []
    for s in series_list:
        cur = "  --" if s.last is None else f"{s.last:5.1f}"
        name = f" ({s.label})" if s.label else ""
        segs.append((f"{s.host}{name} {cur}ms", s.color))
    return segs

def layout_legend(series_list, W):
    lines, cur, width = [], [], 0
    for text, color in legend_segments(series_list):
        seg = len(text) + 3
        if width + seg > W and cur:
            lines.append(cur); cur, width = [], 0
        cur.append((text, color)); width += seg
    if cur:
        lines.append(cur)
    return lines

# ---- curses front end ----------------------------------------------------
def run_curses(stdscr, series_list):
    import curses
    curses.curs_set(0); stdscr.nodelay(True); stdscr.timeout(250)
    cmap = {}
    if curses.has_colors():
        curses.start_color(); curses.use_default_colors()
        use256 = curses.COLORS >= 256
        named = {"black": curses.COLOR_BLACK, "red": curses.COLOR_RED,
                 "green": curses.COLOR_GREEN, "yellow": curses.COLOR_YELLOW,
                 "blue": curses.COLOR_BLUE, "magenta": curses.COLOR_MAGENTA,
                 "cyan": curses.COLOR_CYAN, "white": curses.COLOR_WHITE}
        for i, (nm, (idx, fb)) in enumerate(PALETTE.items(), start=1):
            col = idx if use256 else named.get(fb, curses.COLOR_WHITE)
            try:
                curses.init_pair(i, col, -1)
            except curses.error:
                curses.init_pair(i, named.get(fb, curses.COLOR_WHITE), -1)
            cmap[nm] = curses.color_pair(i)
    while True:
        H, W = stdscr.getmaxyx()
        stdscr.erase()
        stdscr.addnstr(0, 0, " itop · latency · LOG scale (ms) · q to quit ".ljust(W)[:W],
                       W, curses.A_REVERSE)
        legend_lines = layout_legend(series_list, W)
        for li, line in enumerate(legend_lines):
            c = 0
            for text, color in line:
                if c >= W: break
                stdscr.addnstr(1 + li, c, text + "   ", W - c, cmap.get(color, 0) | curses.A_BOLD)
                c += len(text) + 3
        top = 1 + len(legend_lines)
        cg, colg = build_graph_grid(series_list, W, H, top)
        for r, (rowc, rowcol) in enumerate(zip(cg, colg)):
            y = top + r
            if y >= H - 2: break
            for x in range(W):
                ch = rowc[x]
                if ch == " ":
                    continue
                attr = cmap.get(rowcol[x], 0) if rowcol[x] else curses.A_DIM
                try: stdscr.addstr(y, x, ch, attr)
                except curses.error: pass
        try:
            stdscr.addnstr(H - 2, 0, "  oldest ── time ──> now".ljust(W)[:W], W, curses.A_DIM)
            stdscr.addnstr(H - 1, 0, " logarithmic y-axis · lines connect over time".ljust(W)[:W], W, curses.A_DIM)
        except curses.error:
            pass
        stdscr.refresh()
        try:
            k = stdscr.getch()
        except KeyboardInterrupt:
            break
        if k in (ord("q"), ord("Q")):
            break

# ---- self-test (no curses) ----------------------------------------------
def selftest():
    apply_scale(DEFAULT_CONFIG)
    # fake data using RFC5737 documentation IPs (no real/private addresses)
    demo = [("router", "gray", "192.168.0.1", 0.6),
            ("WAN-A gw", "green", "203.0.113.1", 1.2),
            ("WAN-A hop", "olive", "203.0.113.9", 19.0),
            ("WAN-B gw", "orange", "198.51.100.1", 9.0),
            ("WAN-B hop", "brown", "198.51.100.9", 11.0),
            ("WAN-A net", "purple", "1.1.1.1", 22.0),
            ("WAN-B net", "blue", "8.8.8.8", 13.0),
            ("server", "red", "example.com:443", 20.0)]
    series_list = []
    import random
    random.seed(1)
    for label, color, host, base in demo:
        s = Series(label, color, host)
        for _ in range(120):
            s.push(max(0.2, base * (1 + random.uniform(-0.2, 0.2))))
        series_list.append(s)
    W, H = 110, 26
    for line in layout_legend(series_list, W):
        print("LEGEND:", "   ".join(t for t, _ in line))
    top = 1 + len(layout_legend(series_list, W))
    for row in build_graph_grid(series_list, W, H, top)[0]:
        print("".join(row))
    print(f"(selftest OK: {len(series_list)} series)")

# ---- main ----------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="terminal latency monitor with a log-scale graph")
    ap.add_argument("-c", "--config", help="path to config.json")
    ap.add_argument("--selftest", action="store_true", help="render one frame with fake data and exit")
    args = ap.parse_args()

    cfg = load_config(config_path(args.config))
    if args.selftest:
        selftest(); return
    apply_scale(cfg)
    if not cfg.get("targets"):
        sys.stderr.write(f"{APP}: discovering network paths…\n")
    targets = resolve_targets(cfg)
    stop = threading.Event()
    series_list = start_targets(targets, float(cfg["interval_seconds"]), stop)
    if not series_list:
        sys.stderr.write(f"{APP}: no usable targets\n"); sys.exit(1)
    import curses
    try:
        curses.wrapper(run_curses, series_list)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()

if __name__ == "__main__":
    main()
