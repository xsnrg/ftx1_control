#!/usr/bin/env python3
"""
FTX-1 Meter Monitor v1.2.6 - Left meters updated to Hamlib 4.7 supported levels
Only STRENGTH, RFPOWER, SWR, ALC, COMP (no more RFPOWER_METER/VD_METER/ID_METER)
Polling at 1s, send only on user change
"""

import tkinter as tk
from tkinter import ttk
import socket
import sys
import time


class FTX1MeterMonitor:
    def __init__(self, host="localhost", port=4532):
        self.host = host
        self.port = port
        self.sock = None

        self.root = tk.Tk()
        self.root.title("FTX-1 Meter Monitor v1.3 - Hamlib Meters")
        self.root.geometry("540x480")
        self.root.resizable(True, True)
        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)

        self.status_var = tk.StringVar(value=f"Connecting to {host}:{port}...")

        # Standard Hamlib level tokens (requires Hamlib with PR #2010 FTX-1 fixes)
        self.left_meters = {
            "STRENGTH": {
                "hamlib_cmd": "l STRENGTH",
                "scale": lambda r: r,  # dB (negative typical); add converter for S-units if desired
                "tx_only": False,
                "fmt": "{:.0f} dB",
                "max": 20  # for bar graph (focus on positive/strong signals)
            },
            "PO": {
                "hamlib_cmd": "l RFPOWER_METER_WATTS",
                "scale": lambda r: r * 10,  # 0-1 → 0-10W (Field max); change to *6 if battery-only
                "tx_only": True,
                "fmt": "{:.1f} W",
                "max": 10  # Field version scale
            },
            "SWR": {
                "hamlib_cmd": "l SWR",
                "scale": lambda r: r,
                "tx_only": True,
                "fmt": "{:.2f}:1",
                "max": 5.0
            },
            "ALC": {
                "hamlib_cmd": "l ALC",
                "scale": lambda r: r * 10,  # 0-1 → 0-10 scale (common on Yaesu)
                "tx_only": True,
                "fmt": "{:.1f}",
                "max": 10.0
            },
            "COMP": {
                "hamlib_cmd": "l COMP",
                "scale": lambda r: r * 100,  # if 0-1 → percent (AMC on FTX-1)
                "tx_only": True,
                "fmt": "{:.0f}%",
                "max": 100
            },
            "VDD": {
                "hamlib_cmd": "l VD_METER",  # or try "l VDD" if alias
                "scale": lambda r: r,
                "tx_only": False,
                "fmt": "{:.1f} V",
                "max": 15.0  # battery/external supply
            },
            "ID": {
                "hamlib_cmd": "l ID_METER",
                "scale": lambda r: r,
                "tx_only": True,
                "fmt": "{:.1f} A",
                "max": 5.0  # low current on Field (higher with Optima amp)
            },
        }

        self.meter_labels = {}
        self.bar_canvases = {}
        self.smoothed_values = {k: 0.0 for k in self.left_meters}

        self.smoothing_alpha = 0.2
        self.bar_height = 6

        # Your original control variables (unchanged)
        self.power_var = tk.DoubleVar()
        self.preamp_var = tk.StringVar()
        self.att_var = tk.StringVar()
        self.sql_var = tk.DoubleVar()
        self.agc_var = tk.StringVar()
        self.nr_var = tk.StringVar()
        self.nb_var = tk.StringVar()
        self.mode_var = tk.StringVar()
        self.filter_var = tk.StringVar(value="—")
        self.preset_var = tk.BooleanVar(value=False)

        self.last_set = {}
        self.ignore_readback_until = 0.0

        self.startup_sync_done = False
        self.startup_retries = 0
        self.max_startup_retries = 5

        self.sync_in_progress = False
        self.last_user_change_time = time.time()  # init to now to skip early periodic
        self.last_control_sync_time = 0.0
        self.control_sync_interval = 10.0  # seconds
        self.user_debounce_sec = 8.0  # ignore periodic after app change

        self.build_gui()
        self.connect_to_rig()
        print("Connect done — waiting 2s before first sync")
        self.root.after(2000, self._startup_control_sync)

        self.root.after(1000, self._perform_control_sync)  # startup only

    def build_gui(self):
        ttk.Label(self.root, textvariable=self.status_var, font=("Arial", 9)).pack(pady=(8, 4))

        sf = ttk.LabelFrame(self.root, text="Radio Status")
        sf.pack(fill="x", padx=10, pady=5)
        self.freq_var = tk.StringVar(value="—")
        ttk.Label(sf, text="Freq:").grid(row=0, column=0, sticky="e", padx=8, pady=3)
        ttk.Label(sf, textvariable=self.freq_var, font=("Arial", 12, "bold")).grid(row=0, column=1, sticky="w")

        ttk.Label(sf, text="Mode:").grid(row=1, column=0, sticky="e", padx=8, pady=3)
        mode_frame = ttk.Frame(sf)
        mode_frame.grid(row=1, column=1, sticky="w")
        mode_combo = ttk.Combobox(mode_frame, textvariable=self.mode_var,
                                  values=["PRESET", "PKTUSB", "PKTLSB", "USB", "LSB", "CW-U", "CW-L", "AM", "FM",
                                          "RTTY", "DATA-U", "DATA-L"], state="readonly", width=12)
        mode_combo.pack(side="left")
        mode_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_controls())
        self.preset_check = ttk.Checkbutton(mode_frame, text="PRESET", variable=self.preset_var,
                                            command=self.apply_controls)
        self.preset_check.pack(side="left", padx=8)

        ttk.Label(mode_frame, text="Filter:").pack(side="left", padx=(15, 5))
        ttk.Label(mode_frame, textvariable=self.filter_var, font=("Arial", 10)).pack(side="left")

        msf = ttk.LabelFrame(self.root, text="Meters")
        msf.pack(fill="both", expand=True, padx=10, pady=6)
        msf.columnconfigure(0, weight=1)
        msf.columnconfigure(2, weight=1)

        pretty_left = {
            "STRENGTH": "S-Meter",
            "PO": "PO",
            "SWR": "SWR",
            "ALC": "ALC",
            "COMP": "COMP",
            "VDD": "VDD",
            "ID": "ID",
        }

        row = 0
        for m in self.left_meters:  # iterates in dict order: STRENGTH → ID
            label_text = pretty_left.get(m, m)
            ttk.Label(msf, text=f"{label_text}:").grid(row=row, column=0, sticky="e", padx=(10, 4), pady=(6, 1))

            var = tk.StringVar(value="—")
            self.meter_labels[m] = var
            ttk.Label(msf, textvariable=var, font=("Arial", 11, "bold"), width=12, anchor="w").grid(
                row=row, column=1, sticky="w", padx=6)

            canvas = tk.Canvas(msf, width=120, height=self.bar_height, bg="#222", highlightthickness=0)
            canvas.grid(row=row + 1, column=1, sticky="w", padx=6, pady=(0, 8))
            self.bar_canvases[m] = canvas
            self.smoothed_values[m] = 0.0

            row += 2

        # Right column — Controls (unchanged, sits neatly at the top)
        ttk.Label(msf, text="Power (W):").grid(row=0, column=2, sticky="e", padx=(8, 2), pady=4)
        self.power_spin = tk.Spinbox(msf, from_=0.5, to=10.0, increment=0.1, textvariable=self.power_var, width=6,
                                     command=self.apply_controls)
        self.power_spin.grid(row=0, column=3, sticky="w", padx=5)

        ttk.Label(msf, text="Preamp:").grid(row=1, column=2, sticky="e", padx=(8, 2), pady=4)
        self.preamp_combo = ttk.Combobox(msf, textvariable=self.preamp_var, values=["IPO", "AMP1", "AMP2"],
                                         state="readonly", width=8)
        self.preamp_combo.grid(row=1, column=3, sticky="w", padx=5)
        self.preamp_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_controls())

        ttk.Label(msf, text="ATT:").grid(row=2, column=2, sticky="e", padx=(8, 2), pady=4)
        self.att_combo = ttk.Combobox(msf, textvariable=self.att_var, values=["Off", "-6 dB", "-12 dB", "-18 dB"],
                                      state="readonly", width=8)
        self.att_combo.grid(row=2, column=3, sticky="w", padx=5)
        self.att_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_controls())

        ttk.Label(msf, text="Squelch:").grid(row=3, column=2, sticky="e", padx=(8, 2), pady=4)
        self.sql_spin = tk.Spinbox(msf, from_=0.0, to=1.0, increment=0.05, textvariable=self.sql_var, width=6,
                                   command=self.apply_controls)
        self.sql_spin.grid(row=3, column=3, sticky="w", padx=5)

        ttk.Label(msf, text="AGC:").grid(row=4, column=2, sticky="e", padx=(8, 2), pady=4)
        self.agc_combo = ttk.Combobox(msf, textvariable=self.agc_var, values=["Off", "Fast", "Medium", "Slow", "Auto"],
                                      state="readonly", width=10)
        self.agc_combo.grid(row=4, column=3, sticky="w", padx=5)
        self.agc_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_controls())

        ttk.Label(msf, text="Noise Red. (NR):").grid(row=5, column=2, sticky="e", padx=(8, 2), pady=4)
        self.nr_combo = ttk.Combobox(msf, textvariable=self.nr_var,
                                     values=["Off", "1", "2", "3", "4", "5", "6", "7", "8", "9"], state="readonly",
                                     width=8)
        self.nr_combo.grid(row=5, column=3, sticky="w", padx=5)
        self.nr_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_controls())

        ttk.Label(msf, text="Noise Bl. (NB):").grid(row=6, column=2, sticky="e", padx=(8, 2), pady=4)
        self.nb_combo = ttk.Combobox(msf, textvariable=self.nb_var,
                                     values=["Off", "1", "2", "3", "4", "5", "6", "7", "8", "9"], state="readonly",
                                     width=8)
        self.nb_combo.grid(row=6, column=3, sticky="w", padx=5)
        self.nb_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_controls())

        # Reconnect button
        reconnect_btn = ttk.Button(self.root, text="Reconnect", command=self.reconnect)
        reconnect_btn.pack(pady=10)

    def update_meter_gui(self, m, value):
        var = self.meter_labels[m]
        canvas = self.bar_canvases[m]
        cfg = self.left_meters[m]

        disp = cfg["fmt"].format(value)
        var.set(disp)

        canvas.delete("all")
        fill_width = min(value / cfg["max"], 1.0) * 100
        color = "green" if value < cfg["max"] * 0.8 else "orange" if value < cfg["max"] else "red"
        canvas.create_rectangle(0, 0, fill_width, self.bar_height, fill=color, outline="")

    def _perform_control_sync(self, force=False):
        now = time.time()

        if self.sync_in_progress:
            return

        if not force and now - self.last_control_sync_time < self.control_sync_interval:
            return  # too soon for periodic

        if not force and now - self.last_user_change_time < self.user_debounce_sec:
            return  # recent user/app change - skip to avoid fight

        self.sync_in_progress = True
        success = 0

        # Helper: try to parse response and set var/setter
        def try_set(var, resp, parser=float, setter=None, scale=None):
            nonlocal success
            if not resp or not resp.strip() or "RPRT" in resp or "Error" in resp:
                return
            try:
                raw = float(resp.strip())  # most levels are float
                if scale:
                    val = scale(raw)
                else:
                    val = parser(raw)
                if setter:
                    setter(val)
                else:
                    var.set(val)
                success += 1
            except (ValueError, TypeError):
                pass  # silent fail on bad parse

        # RFPOWER: 0.0-1.0 → watts (clamp 0.5-10)
        resp = self.rig_cmd("l RFPOWER")
        print(f"l RFPOWER → {resp}")
        try_set(self.power_var, resp, parser=float,
                scale=lambda r: max(0.5, min(10.0, round(r * 10, 1))))

        # PREAMP: 0=IPO, 1=AMP1, 2=AMP2
        resp = self.rig_cmd("l PREAMP")
        print(f"l PREAMP → {resp}")

        def set_preamp(v):
            map_ = {0: "IPO", 1: "AMP1", 2: "AMP2"}
            self.preamp_var.set(map_.get(int(round(v)), "IPO"))  # round if float

        try_set(None, resp, parser=float, setter=set_preamp)

        # ATT: 0=Off, 6=-6, 12=-12, 18=-18
        resp = self.rig_cmd("l ATT")
        print(f"l ATT → {resp}")

        def set_att(v):
            map_ = {0: "Off", 6: "-6 dB", 12: "-12 dB", 18: "-18 dB"}
            self.att_var.set(map_.get(int(round(v)), "Off"))

        try_set(None, resp, parser=float, setter=set_att)

        # SQL: 0.0-1.0 float
        resp = self.rig_cmd("l SQL")
        print(f"l SQL → {resp}")
        try_set(self.sql_var, resp, parser=float)

        # AGC: 0=Off,1=Fast,2=Medium,3=Slow,4=Auto (confirm with your rig)
        resp = self.rig_cmd("l AGC")
        print(f"l AGC → {resp}")

        def set_agc(v):
            map_ = {0: "Off", 1: "Fast", 2: "Medium", 3: "Slow", 4: "Auto"}
            self.agc_var.set(map_.get(int(round(v)), "Off"))

        try_set(None, resp, parser=float, setter=set_agc)

        # NR (DNR): 0.0-1.0 → map to 0="Off", 1-10
        resp = self.rig_cmd("l NR")
        print(f"l NR → {resp}")

        def set_nr(raw):
            level = int(round(raw * 10))  # 0.0→0, 0.0667→1 (if ~1/15 but CAT is /10), up to 1.0→10
            nr_str = "Off" if level <= 0 else str(min(10, max(0, level)))
            self.nr_var.set(nr_str)

        try_set(None, resp, parser=float, setter=set_nr)

        # NB: assume 0.0-1.0 normalized → scale to int 0-10 (adjust if your poll shows different)
        resp = self.rig_cmd("l NB")
        print(f"l NB → {resp}")

        def set_nb(raw):
            level = int(round(raw * 10))  # common mapping; if 0-100, change to round(raw)
            nb_str = "Off" if level <= 0 else str(min(10, max(0, level)))
            self.nb_var.set(nb_str)

        try_set(None, resp, parser=float, setter=set_nb)

        self.sync_in_progress = False
        self.last_control_sync_time = now

        print(f"Control sync: {success}/7 successful")
        return success

    def _startup_control_sync(self):
        if self.startup_sync_done:
            return

        success = self._perform_control_sync(force=True)

        if success >= 5:
            self.startup_sync_done = True
            self.status_var.set("Startup sync OK ✓")
            self.root.after(200, self.update_readings)  # now start polling
        else:
            self.startup_retries += 1
            if self.startup_retries < self.max_startup_retries:
                self.root.after(4000, self._startup_control_sync)
            else:
                self.startup_sync_done = True
                self.root.after(200, self.update_readings)  # continue anyway

    def apply_controls(self):
        if not self.sock:
            self.status_var.set("Not connected")
            return

        self.last_user_change_time = time.time()
        self.ignore_readback_until = time.time() + 12.0

        power_w = self.power_var.get()
        power_raw = power_w / 10.0
        self.rig_cmd(f"L RFPOWER {power_raw:.2f}")
        self.last_set["power"] = power_raw

        sql_val = self.sql_var.get()
        self.rig_cmd(f"L SQL {sql_val:.2f}")
        self.last_set["sql"] = sql_val

        agc_map = {"Off": 0, "Fast": 1, "Medium": 2, "Slow": 3, "Auto": 6}
        agc_val = agc_map.get(self.agc_var.get(), 0)
        self.rig_cmd(f"L AGC {agc_val}")
        self.last_set["agc"] = agc_val

        # --- Fixed NR handling ---
        nr_display = self.nr_var.get()          # Assume this is a StringVar like "Off", "1", "2", ..., "10"
        if nr_display == "Off":
            nr_int = 0
        else:
            try:
                nr_int = int(nr_display)        # User sees/selects clean integers 1–10
                if not 1 <= nr_int <= 10:
                    nr_int = 0                  # Clamp invalid to off
            except ValueError:
                nr_int = 0                      # Fallback

        # Hamlib expects 0.0 (off) to 1.0 (max=level 10)
        # So map: 0→0.0, 1→0.1, ..., 10→1.0
        nr_normalized = nr_int / 10.0

        self.rig_cmd(f"L NR {nr_normalized:.4f}")   # Consistent precision
        self.last_set["nr"] = nr_normalized         # Or store nr_int if you prefer

        nb_display = self.nb_var.get()
        nb_val = 0 if nb_display == "Off" else int(nb_display)
        self.rig_cmd(f"L NB {nb_val}")
        self.last_set["nb"] = nb_val

        if self.preset_var.get():
            self.rig_cmd("X")
        mode_str = self.mode_var.get()
        self.rig_cmd(f"M {mode_str} 0")
        self.last_set["mode"] = mode_str
        self.last_set["preset"] = self.preset_var.get()

        self.status_var.set("Changes applied")

    def format_smeter(self, raw_str):
        try:
            v = float(raw_str)
            return f"{v:.1f} dB"
        except:
            return "—"

    def format_value(self, name, val):
        if not val: return "—"
        try:
            v = float(val)
            if name == "RFPOWER":
                return f"{v * 10:.1f} W"
            if name == "SWR":
                return f"{v:.2f}"
            if name == "STRENGTH":
                return self.format_smeter(val)
            if name == "COMP":
                return f"{v:.1f}"
            return f"{v:.2f}"
        except:
            return val

    def update_progress_bar(self, name, raw_val):
        canvas = self.bar_canvases.get(name)
        if not canvas: return

        try:
            v = float(raw_val) if raw_val not in ["—", ""] else 0
        except:
            v = 0

        old = self.smoothed_values.get(name, 0.0)
        smoothed = self.smoothing_alpha * v + (1 - self.smoothing_alpha) * old
        self.smoothed_values[name] = smoothed

        canvas.delete("all")

        width = 100
        height = self.bar_height

        if name == "RFPOWER":
            pct = min(smoothed, 1.0)
            fill_color = "lime"
        elif name == "SWR":
            pct = min(max((smoothed - 1.0) / 4.0, 0), 1.0)
            if smoothed > 2.5:
                fill_color = "red"
            elif smoothed > 1.7:
                fill_color = "orange"
            else:
                fill_color = "lime"
        elif name == "ALC":
            pct = min(smoothed, 1.0)
            if smoothed > 0.7:
                fill_color = "red"
            elif smoothed > 0.4:
                fill_color = "orange"
            else:
                fill_color = "lime"
        elif name == "STRENGTH":
            try:
                abs_raw = abs(float(raw_val))
                pct = min(abs_raw / 60.0, 1.0)
            except:
                pct = 0
            old_pct = self.smoothed_values.get(name + "_pct", 0.0)
            smoothed_pct = self.smoothing_alpha * pct + (1 - self.smoothing_alpha) * old_pct
            self.smoothed_values[name + "_pct"] = smoothed_pct
            pct = smoothed_pct
            if pct > 0.9:
                fill_color = "red"
            elif pct > 0.7:
                fill_color = "orange"
            else:
                fill_color = "lime"
        elif name == "COMP":
            pct = min(smoothed, 1.0)
            fill_color = "lime"
        else:
            pct = 0
            fill_color = "gray"

        canvas.create_rectangle(0, 0, width, height, fill="#333", outline="")
        fill_width = int(width * pct)
        canvas.create_rectangle(0, 0, fill_width, height, fill=fill_color, outline="")

    def connect_to_rig(self):
        try:
            if self.sock: self.sock.close()
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(5.0)
            self.sock.connect((self.host, self.port))
            self.status_var.set("Connected ✓")
            return True
        except Exception as e:
            self.status_var.set(f"Connect error: {e}")
            self.sock = None
            return False

    def _read_line(self):
        """Read exactly one \\n-terminated line from the socket, byte by byte."""
        buf = ""
        while True:
            chunk = self.sock.recv(1).decode('ascii', errors='ignore')
            if not chunk or chunk == "\n":
                break
            buf += chunk
        return buf.strip()

    def _drain_socket(self):
        """Non-blocking drain of any stale data in the socket buffer."""
        drained = b""
        self.sock.setblocking(False)
        try:
            while True:
                stale = self.sock.recv(256)
                if not stale:
                    break
                drained += stale
        except BlockingIOError:
            pass
        finally:
            self.sock.setblocking(True)
            self.sock.settimeout(5.0)
        if drained:
            print(f"  drained stale: {drained!r}")
        return drained

    def rig_cmd(self, cmd):
        """Send a command using simple protocol. Read one line response.
        Validates that the response looks reasonable before returning."""
        if not self.sock: return None
        try:
            self.sock.sendall((cmd + "\n").encode('ascii'))
            resp = self._read_line()
            if not resp:
                return None
            if "RPRT" in resp:
                try:
                    code = int(resp.split("RPRT")[1].strip())
                    if code != 0:
                        print(f"rig_cmd('{cmd}') error: RPRT {code}")
                except ValueError:
                    pass
                return None
            return resp
        except Exception as e:
            print(f"Command failed: {cmd} → {e}")
            self.sock = None
            self.status_var.set("Connection dropped - reconnecting...")
            return None

    def rig_cmd_lines(self, cmd, num_lines=2):
        """Send a command that returns multiple response lines (simple protocol).
        Drains socket first to ensure clean state, then reads expected lines.

        Handles both newline-separated responses (default rigctld)
        and custom-separator responses (rigctld -S <char>).
        """
        if not self.sock: return None
        try:
            # Drain stale data to resync before multi-line read
            self._drain_socket()

            self.sock.sendall((cmd + "\n").encode('ascii'))
            lines = []
            attempts = 0
            while len(lines) < num_lines and attempts < num_lines + 4:
                attempts += 1
                line = self._read_line()
                if not line:
                    continue
                if "RPRT" in line:
                    try:
                        code = int(line.split("RPRT")[1].strip())
                        if code != 0:
                            print(f"rig_cmd_lines('{cmd}') error: RPRT {code}")
                    except ValueError:
                        pass
                    break
                lines.append(line)
            # Handle custom separator on a single line
            if len(lines) == 1 and len(lines[0]) > 0:
                for sep in ['$', '@', '|', ';']:
                    if sep in lines[0]:
                        lines = [part.strip() for part in lines[0].split(sep)]
                        break
            return lines if lines else None
        except Exception as e:
            print(f"Command failed: {cmd} → {e}")
            self.sock = None
            self.status_var.set("Connection dropped - reconnecting...")
            return None

    def rig_cmd_extended(self, cmd):
        """Send a command using extended protocol (+). For multi-line responses."""
        if not self.sock: return None
        try:
            self.sock.sendall(("+" + cmd + "\n").encode('ascii'))
            result_lines = []
            while True:
                line = self._read_line()
                if "RPRT" in line:
                    break
                # Skip the echo/command line (e.g. "get_mode:")
                # It ends with ':' but has no value after it
                if line.endswith(":"):
                    continue
                # Extended responses have "Key: Value" format — extract the value
                if ": " in line:
                    line = line.split(": ", 1)[1]
                result_lines.append(line)
            resp = "\n".join(result_lines)
            return resp if resp else None
        except Exception as e:
            print(f"Command failed: {cmd} → {e}")
            self.sock = None
            self.status_var.set("Connection dropped - reconnecting...")
            return None

    def get_hamlib_level(self, cmd):

        if not self.sock:
            print(f"get_hamlib_level: no socket for {cmd}")
            return None

        try:
            # Use the existing rig_cmd that already works well
            resp = self.rig_cmd(cmd)

            if resp is None:
                return None

            resp = resp.strip()

            # Check for error response
            if "RPRT" in resp:
                if "RPRT 0" not in resp:
                    print(f"get_hamlib_level: error response for {cmd}: {resp}")
                    return None
                # Extract just the value line (before RPRT)
                lines = resp.splitlines()
                if lines:
                    return lines[0].strip()
                else:
                    return None

            # If no RPRT at all (unusual but possible), assume whole response is value
            return resp

        except Exception as e:
            print(f"get_hamlib_level exception for {cmd}: {e}")
            return None

    def update_readings(self):
        now = time.time()
        if now - self.last_control_sync_time > self.control_sync_interval:
            if time.time() >= self.ignore_readback_until:
                self._perform_control_sync()

        if not self.sock:
            self.status_var.set("Disconnected — retrying...")
            self.root.after(3000, self.reconnect)
            self.root.after(1000, self.update_readings)
            return

        try:
            # Drain any stale data at the start of each poll cycle
            self._drain_socket()

            # Frequency
            f = self.rig_cmd("f")
            if f and f.replace(".", "").replace("-", "").isdigit():
                try:
                    freq_mhz = float(f) / 1_000_000
                    self.freq_var.set(f"{freq_mhz:.6f} MHz")
                except ValueError:
                    self.freq_var.set("—")

            # Mode + Filter
            mode_lines = self.rig_cmd_lines("m", num_lines=2)
            if mode_lines and len(mode_lines) >= 2:
                self.mode_var.set(mode_lines[0].strip() or "—")
                self.filter_var.set(f"{mode_lines[1].strip()} Hz" or "—")
            elif mode_lines:
                self.mode_var.set(mode_lines[0].strip() or "—")
                self.filter_var.set("—")

            # Meters
            for name, cfg in self.left_meters.items():
                raw_str = self.get_hamlib_level(cfg["hamlib_cmd"])

                if raw_str is None:
                    self.update_meter_gui(name, 0.0)
                    continue

                try:
                    raw = float(raw_str)
                except (ValueError, TypeError):
                    print(f"Meter {name} conversion failed: {raw_str!r}")
                    self.update_meter_gui(name, 0.0)
                    continue

                value = cfg["scale"](raw)

                # Skip display if tx-only meter and value is zero (likely not transmitting)
                if cfg.get("tx_only", False) and value <= 0.0:
                    self.update_meter_gui(name, 0.0)
                    continue

                # Display the scaled value directly (no smoothing)
                self.update_meter_gui(name, value)

        except Exception as e:
            print(f"Poll cycle error: {e}")

        # Status line
        self.status_var.set("Connected ✓")

        # Schedule next update
        self.root.after(1000, self.update_readings)

    def reconnect(self):
        self.connect_to_rig()
        self.status_var.set("Reconnecting...")

    def quit_app(self):
        if hasattr(self, 'sock') and self.sock:
            try:
                self.sock.close()
            except:
                pass
        self.root.destroy()


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "localhost"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 4532
    app = FTX1MeterMonitor(host, port)
    app.root.mainloop()

