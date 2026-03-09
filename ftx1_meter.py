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
        self.root.title("FTX-1 Meter Monitor v1.3 - Raw RM Meters")
        self.root.geometry("540x480")
        self.root.resizable(True, True)
        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)

        self.status_var = tk.StringVar(value=f"Connecting to {host}:{port}...")

        # Raw RM meters using correct Yaesu CAT codes (bypasses Hamlib bug)
        self.left_meters = {
            "STRENGTH": {"rm": 1, "scale": lambda r: r / 2.55, "tx_only": False, "fmt": "{:.0f} dB", "max": 100},
            "PO": {"rm": 5, "scale": lambda r: r / 2.55, "tx_only": True, "fmt": "{:.1f} W", "max": 100},
            "SWR": {"rm": 6, "scale": lambda r: 1.0 + (r / 50.0), "tx_only": True, "fmt": "{:.2f}:1", "max": 5.0},
            "ALC": {"rm": 4, "scale": lambda r: min(r / 25.5, 10.0), "tx_only": True, "fmt": "{:.1f}", "max": 10.0},
            "COMP": {"rm": 3, "scale": lambda r: r / 2.55, "tx_only": True, "fmt": "{:.0f}%", "max": 100},
            "VDD": {"rm": 8, "scale": lambda r: r / 15, "tx_only": False, "fmt": "{:.1f} V", "max": 15.0},
            "ID": {"rm": 7, "scale": lambda r: r / 25.5, "tx_only": True, "fmt": "{:.1f} A", "max": 10.0},
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

        self.build_gui()
        self.connect_to_rig()
        self.root.after(800, self._sync_controls_from_radio)
        self.root.after(1000, self.update_readings)

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

    def _sync_controls_from_radio(self):
        if not self.sock:
            self.status_var.set("Cannot sync - not connected")
            return
        success = 0
        print("--- Startup sync started ---")

        # RFPOWER
        resp = self.rig_cmd("l RFPOWER")
        print(f"l RFPOWER → {resp}")
        if resp and "Level Value:" in resp:
            try:
                raw = float(resp.split("Level Value:")[-1].strip())
                self.power_var.set(round(raw * 10, 1))
                success += 1
            except Exception as e:
                print(f"RFPOWER error: {e}")

        # PREAMP
        resp = self.rig_cmd("l PREAMP")
        print(f"l PREAMP → {resp}")
        if resp and "Level Value:" in resp:
            try:
                val = int(float(resp.split("Level Value:")[-1].strip()))
                preamp_map = {0: "IPO", 1: "AMP1", 2: "AMP2"}
                self.preamp_var.set(preamp_map.get(val, "IPO"))
                success += 1
            except:
                pass

        # ATT
        resp = self.rig_cmd("l ATT")
        print(f"l ATT → {resp}")
        if resp and "Level Value:" in resp:
            try:
                val = int(float(resp.split("Level Value:")[-1].strip()))
                att_map = {0: "Off", 6: "-6 dB", 12: "-12 dB", 18: "-18 dB"}
                self.att_var.set(att_map.get(val, "Off"))
                success += 1
            except:
                pass

        # SQL
        resp = self.rig_cmd("l SQL")
        print(f"l SQL → {resp}")
        if resp and "Level Value:" in resp:
            try:
                val = float(resp.split("Level Value:")[-1].strip())
                self.sql_var.set(val)
                success += 1
            except:
                pass

        # AGC
        resp = self.rig_cmd("l AGC")
        print(f"l AGC → {resp}")
        if resp and "Level Value:" in resp:
            try:
                val = int(float(resp.split("Level Value:")[-1].strip()))
                agc_map = {0: "Off", 1: "Fast", 2: "Medium", 3: "Slow", 4: "Auto"}
                self.agc_var.set(agc_map.get(val, "Off"))
                success += 1
            except:
                pass

        # NR
        resp = self.rig_cmd("l NR")
        print(f"l NR → {resp}")
        if resp and "Level Value:" in resp:
            try:
                val = int(float(resp.split("Level Value:")[-1].strip()))
                self.nr_var.set(str(val))
                success += 1
            except:
                pass

        # NB
        resp = self.rig_cmd("l NB")
        print(f"l NB → {resp}")
        if resp and "Level Value:" in resp:
            try:
                val = int(float(resp.split("Level Value:")[-1].strip()))
                self.nb_var.set(str(val))
                success += 1
            except:
                pass

        self.status_var.set(f"Startup sync: {success}/8 settings read")
        print(f"--- Startup sync complete: {success} successful ---")

    def apply_controls(self):
        if not self.sock:
            self.status_var.set("Not connected")
            return

        self.ignore_readback_until = time.time() + 12.0

        power_w = self.power_var.get()
        power_raw = power_w / 10.0
        self.rig_cmd(f"L RFPOWER {power_raw:.4f}")
        self.last_set["power"] = power_raw

        sql_val = self.sql_var.get()
        self.rig_cmd(f"L SQL {sql_val:.2f}")
        self.last_set["sql"] = sql_val

        agc_map = {"Off": 0, "Fast": 1, "Medium": 2, "Slow": 3, "Auto": 6}
        agc_val = agc_map.get(self.agc_var.get(), 0)
        self.rig_cmd(f"L AGC {agc_val}")
        self.last_set["agc"] = agc_val

        nr_display = self.nr_var.get()
        nr_val = 0 if nr_display == "Off" else int(nr_display)
        self.rig_cmd(f"L NR {nr_val / 9.0:.2f}")
        self.last_set["nr"] = nr_val

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

    def rig_cmd(self, cmd):
        if not self.sock: return None
        try:
            self.sock.sendall((cmd + "\n").encode('ascii'))
            resp = self.sock.recv(1024).decode('ascii', errors='ignore').strip()
            if "RPRT" in resp:
                resp = resp.split("RPRT", 1)[0].strip()
            return resp if resp else None
        except Exception as e:
            print(f"Command failed: {cmd} → {e}")
            self.sock = None
            self.status_var.set("Connection dropped - reconnecting...")
            return None

    def get_raw_meter(self, rm_num):
        """Read raw Yaesu RM meter value (0-255). Robust against real responses like 'RM7070000;'."""
        try:
            resp = self.rig_cmd(f"w RM{rm_num};")
            if not resp or not resp.startswith(f"RM{rm_num}") or ";" not in resp:
                return 0

            # Extract first 3 digits after "RMx" — works for RM7070000;, RM5123000;, etc.
            data = resp[len(f"RM{rm_num}"):].split(';')[0].strip()
            if len(data) >= 3 and data[:3].isdigit():
                raw = int(data[:3])
                # print(f"DEBUG RM{rm_num} → raw={raw} (resp={repr(resp)})")  # uncomment during testing
                return raw
            return 0
        except Exception as e:
            print(f"RM{rm_num} read error: {e}")
            return 0

    def update_readings(self):
        """Clean 1-second meter + status poll (ID now works reliably)."""
        if not self.sock:
            self.status_var.set("Disconnected — retrying...")
            self.root.after(3000, self.reconnect)
            self.root.after(1000, self.update_readings)
            return

        try:
            # --- 1. Basic status (freq + mode) ---
            f = self.rig_cmd("f")
            if f and f.replace(".", "").isdigit():
                self.freq_var.set(f"{float(f) / 1_000_000:.6f} MHz")

            m = self.rig_cmd("m")
            if m:
                parts = m.split()
                self.mode_var.set(parts[0] if parts else "—")
                self.filter_var.set(parts[1] + " Hz" if len(parts) > 1 else "—")

            # --- 2. Control readback (skip right after user change) ---
            if time.time() >= self.ignore_readback_until:
                self._sync_controls_from_radio()          # your existing sync logic (kept separate)

            # --- 3. TX detection (much more reliable than old l RFPOWER) ---
            po_raw = self.get_raw_meter(5)                # RM5 = PO
            is_tx = po_raw > 8                            # ~3% of scale = transmitting

            # --- 4. All meters (uniform, clean, ID now works) ---
            for name, cfg in self.left_meters.items():
                if cfg.get("tx_only", False) and not is_tx:
                    self.update_meter_gui(name, 0.0)
                    self.smoothed_values[name] = 0.0
                    continue

                raw = self.get_raw_meter(cfg["rm"])
                value = cfg["scale"](raw)

                # EMA smoothing
                prev = self.smoothed_values[name]
                smoothed = self.smoothing_alpha * prev + (1 - self.smoothing_alpha) * value
                self.smoothed_values[name] = smoothed

                self.update_meter_gui(name, smoothed)

        except Exception as e:
            print(f"Poll cycle error: {e}")

        # Status line
        self.status_var.set("Connected ✓")
        self.root.after(1000, self.update_readings)

    def reconnect(self):
        self.connect_to_rig()
        self.status_var.set("Reconnecting...")

    def quit_app(self):
        """Clean shutdown when window is closed"""
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
