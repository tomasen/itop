# itop

A terminal **network health monitor** with a **logarithmic** latency graph.

`ping`/`gping` draw everything on one linear axis, so a ~0.5 ms LAN line and a
~25 ms internet line can't both be readable at once — the fast one collapses onto
the floor. `itop` uses a **log y-axis**, so every line gets room, and draws smooth
**braille** lines like a real chart. The legend shows each target's IP and a
friendly name.

Point it at any setup — a **single connection**, or a **multi-WAN / load-balanced**
router. It auto-detects your WAN(s) and breaks each one out into its gateway,
next hop, and internet latency — including *intermediate hops* you normally can't
ping directly.

```
 itop · latency · LOG scale (ms) · q to quit
gw 0.6ms   wan-a hop 19ms   wan-a net 22ms   wan-b net 14ms   server 20ms ...
   200┤··················································································
    20┤···············⢀⡠⠤⢄⣀⡠⠤⠒⠉⠉⠒⠤⢄⣀⠤⠔⠒⠉⠉⠉⠒⠢⠤⣀⡠⠤⠒⠉⠉⠒⠤⢄⣀⠤⠔⠒⠉⠉⠉⠒⠢⠤⣀
    10┤·····················································································
     1┤⢀⣀⡠⠤⠔⠒⠉⠉⠒⠤⢄⣀⡠⠤⠒⠉⠒⠢⠤⣀⡠⠤⠔⠒⠉⠉⠉⠒⠢⠤⢄⣀⠤⠔⠒⠉⠒⠢⠤⢄⣀⠤⠤⠒⠉⠉⠉⠒⠤
   0.5┤··················································································
```

## Requirements

- Python 3.8+ (standard library only — `curses` is included on macOS/Linux)
- `ping` and `traceroute` on `PATH` (preinstalled on macOS; `apt install traceroute` on Debian/Ubuntu)
- A terminal whose font has braille glyphs (most modern ones do)

## Install

```sh
git clone https://github.com/tomasen/itop.git
cd itop
./itop.py            # runs immediately with sensible defaults
```

Optionally drop it on your `PATH`:

```sh
ln -s "$PWD/itop.py" /usr/local/bin/itop
```

**No config needed.** On startup `itop` auto-discovers your network: it probes
several public IPs, groups them by their first hop to identify each WAN, then
graphs your router plus every WAN's **gateway**, **next hop**, and **internet**
latency — one WAN or several, figured out for you.

## Configure (optional)

You only need a config to *add* something (like a server you care about) or to
take full manual control. Create `~/.config/itop/config.json`:

```json
{
  "extra_targets": [
    {"label": "my server", "kind": "tcp", "host": "example.com", "port": 443, "color": "red"}
  ]
}
```

- `extra_targets` — appended to the auto-discovered lines.
- `targets` — if present, fully **replaces** discovery (manual mode).

See [`config.example.json`](config.example.json). Each target has a `kind`:

| kind | fields | what it measures |
|------|--------|------------------|
| `icmp` | `host` (or `"auto"` = default gateway) | normal ping RTT |
| `tcp`  | `host`, `port` | TCP connect time (for hosts that block ICMP) |
| `hop`  | `host`, `ttl` | latency of the router `ttl` hops away, via a TTL-limited probe toward `host` |

Colors: `gray green olive orange brown purple magenta blue teal red`
(dark, saturated tones that read on light *or* dark backgrounds).

### Why `hop` exists (multi-WAN)

On a load-balanced multi-WAN router you can't reliably `ping` an intermediate hop:
the router hashes the destination to *some* WAN, and CGNAT hop addresses only
exist on their own WAN. Instead, `hop` sends a probe toward a destination that's
pinned to one WAN (e.g. `1.1.1.1`) but with a small **TTL**, so it expires *at*
the hop you care about and that router answers with "time exceeded". Routing
follows the final destination, so you reliably measure that WAN's hop.

Find which destination pins to which WAN with `traceroute -m2 1.1.1.1`.

## Keys

- `q` / `Ctrl-C` — quit

## License

MIT — see [LICENSE](LICENSE).
