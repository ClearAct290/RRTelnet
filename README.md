# MyBBSHome

A tiny telnet **home page and launcher for your BBS**.

People telnet into MyBBSHome and see your ASCII logo, a short description and an
arrow-key menu of BBSes (or any telnet service). Pick one with Enter and MyBBSHome
connects you straight through to it. Everything is plain black-and-white text, so it
works with any telnet client: no ANSI colour, PETSCII or special terminal required.

```
                       Created with MyBBSHome

                __  __       ____  ____ ____  _   _
               |  \/  |_   _| __ )| __ ) ___|| | | | ___  _ __ ___   ___
               | |\/| | | | |  _ \|  _ \___ \| |_| |/ _ \| '_ ` _ \ / _ \
               | |  | | |_| | |_) | |_) |__) |  _  | (_) | | | | | |  __/
               |_|  |_|\__, |____/|____/____/|_| |_|\___/|_| |_| |_|\___|
                       |___/

                 Configure your MyBBSHome to make it yours!

                              > EXAMPLE <

                Up/Down: select   Enter: connect   Q: quit
```

## What it's for

- One front door for several BBSes: share a single address instead of a list of them.
- Reach machines on your **home network** (a C64, a Raspberry Pi, a retro PC) from the
  internet through one forwarded port, without telling callers their addresses.
- Optional sign-in once: your username and password are passed on to the BBS you pick.

## Requirements

Python 3.8 or newer. No third-party packages.

## Setup

1. Put `mybbshome.py` in a folder and run it:

   ```
   python mybbshome.py
   ```

   On first run it creates `mybbshome.ini`, `logo.txt` and `list.txt` next to the script.
   You can also pass a config path: `python mybbshome.py /path/to/mybbshome.ini`.

2. Test it locally:

   ```
   telnet localhost 2300
   ```

3. Edit `list.txt`, `logo.txt` and `mybbshome.ini` to make it yours (below).

4. To let others connect, forward a port on your router to the machine running
   MyBBSHome. The default port is **2300** (needs no root/admin rights). To use the standard
   telnet port, either set `port = 23` in the ini (needs root/admin) or forward external
   port 23 to internal port 2300.

Logo, text and `list.txt` are re-read for every visitor, so changes show up without a
restart. Only the `[server]` section of the ini needs one.

## The list (`list.txt`)

One entry per line, a label and an address:

```
TLE="EXAMPLE" ADS="example.com:23"
```

- `TLE` is the label shown in the menu, `ADS` is `host:port`. The port is optional (default 23).
- A line starting with `#` is ignored: use it for comments or to switch entries off.
- Add `HIDEIP` at the end of a line to hide that entry's address from visitors:

  ```
  TLE="Secret BBS" ADS="bbs.example.net:2323" HIDEIP
  ```

### Local addresses

You can list machines on your own network:

```
TLE="My C64" ADS="192.168.1.50:6400"
TLE="Pi BBS" ADS="10.0.0.7"
TLE="This PC" ADS="127.0.0.1:2323"
```

MyBBSHome makes the connection from the machine it runs on, so a visitor from the
internet is effectively port-forwarded to the local machine through your network. Local
means private, loopback, link-local and similar addresses (`192.168.*`, `10.*`,
`172.16-31.*`, `127.*`, `169.254.*`, `100.64/10`), plus `localhost`, `*.local`, `*.lan`
style names and any name that *resolves* to such an address.

**The address of a local entry is always hidden from visitors. This can't be turned off.**
Visitors only see "Connecting to My C64 ..." and never the IP or port. `HIDEIP` does the
same for entries with public addresses.

> **Security note:** visitors can only reach what you put in `list.txt`. They cannot type
> in their own address. Still, every entry is reachable by anyone who can reach your home
> page, so only list machines you are happy to expose, and consider `allow_ips` in the ini.

## Logo and text

- `logo.txt` holds your ASCII logo. The default is the MyBBSHome logo.
- In `mybbshome.ini`, `[home]` sets the line above the logo (`created_text`, default
  "Created with MyBBSHome"), the description below it (`description`), centering, the help
  line and the goodbye text.
- `highlight` is `marker` (`> Label <`), `reverse` (inverse video) or `both`.

## Login pass-through

Set `prompt = true` under `[login]` and visitors are asked for a username and password
first (blank username = skip). After connecting, MyBBSHome watches for the BBS's own login
and password prompts and types the credentials in for them. The details are not checked
or stored, and never logged. The prompt patterns (`username_prompt`, `password_prompt`)
and the line ending (`newline`) are configurable if a BBS doesn't respond.

## Other settings

| Section | Key | Meaning |
|---|---|---|
| `[server]` | `host`, `port` | where to listen |
| `[server]` | `max_clients`, `max_clients_per_ip` | connection limits |
| `[server]` | `idle_timeout` | seconds idle on the home page before disconnect (0 = never) |
| `[server]` | `allow_ips`, `deny_ips` | comma separated IPs / CIDR ranges |
| `[connect]` | `timeout` | seconds to wait for a BBS to answer |
| `[connect]` | `return_after_disconnect` | land back on the home page when a BBS hangs up |
| `[logging]` | `level`, `file` | log level and optional log file |

## How it works

MyBBSHome is a small `asyncio` telnet server. It handles the home page itself, then opens
a TCP connection to the chosen address and relays bytes in both directions untouched,
including telnet negotiation, so the remote BBS talks to the visitor's client directly.
It is a relay, not a direct hand-off: traffic passes through the machine running MyBBSHome.

## License

Copyright (C) 2026 ClearAct290-Reddit

MyBBSHome is free software, released under the
[GNU General Public License, version 2](LICENSE). See `LICENSE` for the full text.
