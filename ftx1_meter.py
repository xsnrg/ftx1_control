#!/usr/bin/env python3
"""
FTX-1 Meter Monitor v1.2.5 - Polling at 1 second, send only on user change
Read-back is read-only, no sending during polling cycles
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
        self.root.title("FTX-1 Meter Monitor v1.2.5")
        self.root.geometry("540x520")
        self.root.resizable(True, True)
        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)

        self.status_var = tk.StringVar(value=f"Connecting to {host}:{port}...")

        self.left_meters = ["STRENGTH", "RFPOWER_METER", "SWR", "ALC", "COMP_METER", "VD_METER", "ID_METER"]
        self.meter_labels = {}
        self.bar_canvases = {}
        self.smoothed_values = {}

        self.smoothing_alpha = 0.4
        self.bar_height = 6

        # Control variables
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
        self.root.after(800, self.sync_controls_from_radio)
        self.root.after(1000, self.update_readings)  # Poll every 1 second

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
        mode_combo = ttk.Combobox(mode_frame, textvariable=self.mode_var, values=["PRESET", "PKTUSB", "PKTLSB", "USB", "LSB", "CW-U", "CW-L", "AM", "FM", "RTTY", "DATA-U", "DATA-L"], state="readonly", width=12)
        mode_combo.pack(side="left")
        mode_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_controls())
        self.preset_check = ttk.Checkbutton(mode_frame, text="PRESET", variable=self.preset_var, command=self.apply_controls)
        self.preset_check.pack(side="left", padx=8)

        ttk.Label(mode_frame, text="Filter:").pack(side="left", padx=(15,5))
        ttk.Label(mode_frame, textvariable=self.filter_var, font=("Arial", 10)).pack(side="left")

        msf = ttk.LabelFrame(self.root, text="Meters / Status")
        msf.pack(fill="both", expand=True, padx=10, pady=6)
        msf.columnconfigure(0, weight=1)
        msf.columnconfigure(2, weight=1)

        pretty_left = {
            "STRENGTH": "S-Meter",
            "RFPOWER_METER": "PO",
            "SWR": "SWR",
            "ALC": "ALC",
            "COMP_METER": "COMP",
            "VD_METER": "VDD (V)",
            "ID_METER": "ID (A)"
        }
        for i, m in enumerate(self.left_meters):
            row_val = i * 2
            ttk.Label(msf, text=f"{pretty_left.get(m, m)}:").grid(row=row_val, column=0, sticky="e", padx=(8,2), pady=(6,1))
            var = tk.StringVar(value="—")
            self.meter_labels[m] = var
            ttk.Label(msf, textvariable=var, font=("Arial", 11, "bold"), width=12, anchor="w").grid(row=row_val, column=1, sticky="w", padx=5)

            canvas = tk.Canvas(msf, width=100, height=self.bar_height, bg="#333", highlightthickness=0)
            canvas.grid(row=row_val + 1, column=1, sticky="w", padx=5, pady=(0,6))
            self.bar_canvases[m] = canvas
            self.smoothed_values[m] = 0.0

        # Right column - Controls
        ttk.Label(msf, text="Power (W):").grid(row=0, column=2, sticky="e", padx=(8,2), pady=4)
        self.power_spin = tk.Spinbox(msf, from_=0.5, to=10.0, increment=0.1, textvariable=self.power_var, width=6, command=self.apply_controls)
        self.power_spin.grid(row=0, column=3, sticky="w", padx=5)

        ttk.Label(msf, text="Preamp:").grid(row=1, column=2, sticky="e", padx=(8,2), pady=4)
        self.preamp_combo = ttk.Combobox(msf, textvariable=self.preamp_var, values=["IPO", "AMP1", "AMP2"], state="readonly", width=8)
        self.preamp_combo.grid(row=1, column=3, sticky="w", padx=5)
        self.preamp_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_controls())

        ttk.Label(msf, text="ATT:").grid(row=2, column=2, sticky="e", padx=(8,2), pady=4)
        self.att_combo = ttk.Combobox(msf, textvariable=self.att_var, values=["Off", "-6 dB", "-12 dB", "-18 dB"], state="readonly", width=8)
        self.att_combo.grid(row=2, column=3, sticky="w", padx=5)
        self.att_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_controls())

        ttk.Label(msf, text="Squelch:").grid(row=3, column=2, sticky="e", padx=(8,2), pady=4)
        self.sql_spin = tk.Spinbox(msf, from_=0.0, to=1.0, increment=0.05, textvariable=self.sql_var, width=6, command=self.apply_controls)
        self.sql_spin.grid(row=3, column=3, sticky="w", padx=5)

        ttk.Label(msf, text="AGC:").grid(row=4, column=2, sticky="e", padx=(8,2), pady=4)
        self.agc_combo = ttk.Combobox(msf, textvariable=self.agc_var, values=["Off", "Fast", "Medium", "Slow", "Auto"], state="readonly", width=10)
        self.agc_combo.grid(row=4, column=3, sticky="w", padx=5)
        self.agc_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_controls())

        ttk.Label(msf, text="Noise Red. (NR):").grid(row=5, column=2, sticky="e", padx=(8,2), pady=4)
        self.nr_combo = ttk.Combobox(msf, textvariable=self.nr_var, values=["Off", "1", "2", "3", "4", "5", "6", "7", "8", "9"], state="readonly", width=8)
        self.nr_combo.grid(row=5, column=3, sticky="w", padx=5)
        self.nr_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_controls())

        ttk.Label(msf, text="Noise Bl. (NB):").grid(row=6, column=2, sticky="e", padx=(8,2), pady=4)
        self.nb_combo = ttk.Combobox(msf, textvariable=self.nb_var, values=["Off", "1", "2", "3", "4", "5", "6", "7", "8", "9"], state="readonly", width=8)
        self.nb_combo.grid(row=6, column=3, sticky="w", padx=5)
        self.nb_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_controls())

        # Reconnect button
        reconnect_btn = ttk.Button(self.root, text="Reconnect", command=self.reconnect)
        reconnect_btn.pack(pady=10)

    def sync_controls_from_radio(self):
        if not self.sock:
            self.status_var.set("Cannot sync - not connected")
            return

        success = 0
        try:
            pwr_raw = self.rig_cmd("l RFPOWER")
            if pwr_raw:
                try:
                    raw = float(pwr_raw)
                    disp_w = raw * 10
                    self.power_var.set(round(disp_w, 1))
                    self.last_set["power"] = raw
                    success += 1
                except:
                    pass

            preamp_raw = self.rig_cmd("l PREAMP")
            if preamp_raw:
                preamp_map_rev = {0: "IPO", 1: "AMP1", 2: "AMP2"}
                try:
                    disp = preamp_map_rev.get(int(float(preamp_raw)), "IPO")
                    self.preamp_var.set(disp)
                    self.last_set["preamp"] = int(float(preamp_raw))
                    success += 1
                except:
                    pass

            att_raw = self.rig_cmd("l ATT")
            if att_raw:
                att_map_rev = {0: "Off", 6: "-6 dB", 12: "-12 dB", 18: "-18 dB"}
                try:
                    disp = att_map_rev.get(int(float(att_raw)), "Off")
                    self.att_var.set(disp)
                    self.last_set["att"] = int(float(att_raw))
                    success += 1
                except:
                    pass

            sql_raw = self.rig_cmd("l SQL")
            if sql_raw:
                try:
                    self.sql_var.set(round(float(sql_raw), 2))
                    self.last_set["sql"] = float(sql_raw)
                    success += 1
                except:
                    pass

            agc_raw = self.rig_cmd("l AGC")
            if agc_raw:
                agc_map_rev = {0: "Off", 1: "Fast", 2: "Medium", 3: "Slow", 6: "Auto"}
                try:
                    disp = agc_map_rev.get(int(float(agc_raw)), "Off")
                    self.agc_var.set(disp)
                    self.last_set["agc"] = int(float(agc_raw))
                    success += 1
                except:
                    pass

            nr_raw = self.rig_cmd("l NR")
            if nr_raw:
                try:
                    raw_nr = float(nr_raw)
                    nr_level = round(raw_nr * 9)
                    self.nr_var.set(str(nr_level))
                    self.last_set["nr"] = nr_level
                    success += 1
                except:
                    self.nr_var.set("Off")

            nb_raw = self.rig_cmd("l NB")
            if nb_raw:
                try:
                    nb_level = int(float(nb_raw))
                    self.nb_var.set(str(nb_level))
                    self.last_set["nb"] = nb_level
                    success += 1
                except:
                    self.nb_var.set("Off")

            m = self.rig_cmd("m")
            if m:
                parts = m.split()
                if len(parts) >= 1:
                    mode_clean = parts[0]
                    self.mode_var.set(mode_clean)

                    if len(parts) >= 2:
                        self.filter_var.set(parts[1] + " Hz")
                    else:
                        self.filter_var.set("—")
                else:
                    self.mode_var.set(m)
                    self.filter_var.set("—")
                success += 1

            self.preset_var.set(False)
            self.last_set["preset"] = False

            self.status_var.set(f"Startup sync: {success}/8 settings read")

        except Exception as e:
            print(f"Startup sync error: {e}")
            self.status_var.set("Partial startup sync")

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
            if name == "RFPOWER_METER":
                return f"{v * 10:.1f} W"
            if name == "SWR":
                return f"{v:.2f}"
            if name == "STRENGTH":
                return self.format_smeter(val)
            if name == "ID_METER":
                return f"{v / 10:.2f} A"
            if name == "VD_METER":
                return f"{v:.2f} V"
            if name == "COMP_METER":
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

        if name == "RFPOWER_METER":
            pct = min(smoothed, 1.0)
            fill_color = "lime"
        elif name == "SWR":
            pct = min(max((smoothed - 1.0) / 4.0, 0), 1.0)
            if smoothed > 2.5: fill_color = "red"
            elif smoothed > 1.7: fill_color = "orange"
            else: fill_color = "lime"
        elif name == "ALC":
            pct = min(smoothed, 1.0)
            if smoothed > 0.7: fill_color = "red"
            elif smoothed > 0.4: fill_color = "orange"
            else: fill_color = "lime"
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
            if pct > 0.9: fill_color = "red"
            elif pct > 0.7: fill_color = "orange"
            else: fill_color = "lime"
        elif name == "COMP_METER":
            pct = min(smoothed, 1.0)
            fill_color = "lime"
        elif name == "VD_METER":
            pct = min(smoothed / 20.0, 1.0)
            fill_color = "lime"
        elif name == "ID_METER":
            pct = min(smoothed / 10.0, 1.0)
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

    def update_readings(self):
        start = time.time()

        if not self.sock:
            self.root.after(3000, self.reconnect)
            self.root.after(2000, self.update_readings)
            return

        try:
            f = self.rig_cmd("f")
            if f and f.replace(".", "").isdigit():
                self.freq_var.set(f"{float(f)/1_000_000:.6f} MHz")

            m = self.rig_cmd("m")
            if m:
                parts = m.split()
                if len(parts) >= 1:
                    mode_clean = parts[0]
                    self.mode_var.set(mode_clean)

                    if len(parts) >= 2:
                        self.filter_var.set(parts[1] + " Hz")
                    else:
                        self.filter_var.set("—")
                else:
                    self.mode_var.set(m)
                    self.filter_var.set("—")

            for name in self.left_meters:
                val = self.rig_cmd(f"l {name}")
                display_val = self.format_value(name, val)
                self.meter_labels[name].set(display_val)
                self.update_progress_bar(name, val)

            # Read-back for controls - skip if recently changed
            if time.time() < self.ignore_readback_until:
                self.status_var.set("Waiting for radio to apply changes...")
            else:
                pwr_raw = self.rig_cmd("l RFPOWER")
                if pwr_raw:
                    try:
                        raw = float(pwr_raw)
                        disp_w = raw * 10
                        self.power_var.set(round(disp_w, 1))
                        self.power_spin.config(foreground="green" if abs(disp_w - self.last_set.get("power", 0) * 10) < 0.5 else "black")
                    except:
                        self.power_spin.config(foreground="black")

                preamp_raw = self.rig_cmd("l PREAMP")
                if preamp_raw:
                    preamp_map_rev = {0: "IPO", 1: "AMP1", 2: "AMP2"}
                    try:
                        disp = preamp_map_rev.get(int(float(preamp_raw)), "IPO")
                        self.preamp_var.set(disp)
                        self.preamp_combo.config(foreground="green" if disp == self.preamp_var.get() else "black")
                    except:
                        self.preamp_combo.config(foreground="black")

                att_raw = self.rig_cmd("l ATT")
                if att_raw:
                    att_map_rev = {0: "Off", 6: "-6 dB", 12: "-12 dB", 18: "-18 dB"}
                    try:
                        disp = att_map_rev.get(int(float(att_raw)), "Off")
                        self.att_var.set(disp)
                        self.att_combo.config(foreground="green" if disp == self.att_var.get() else "black")
                    except:
                        self.att_combo.config(foreground="black")

                sql_raw = self.rig_cmd("l SQL")
                if sql_raw:
                    try:
                        self.sql_var.set(round(float(sql_raw), 2))
                        self.sql_spin.config(foreground="green" if abs(float(sql_raw) - self.last_set.get("sql", 0.0)) < 0.05 else "black")
                    except:
                        self.sql_spin.config(foreground="black")

                agc_raw = self.rig_cmd("l AGC")
                if agc_raw:
                    agc_map_rev = {0: "Off", 1: "Fast", 2: "Medium", 3: "Slow", 6: "Auto"}
                    try:
                        disp = agc_map_rev.get(int(float(agc_raw)), "Off")
                        self.agc_var.set(disp)
                        self.agc_combo.config(foreground="green" if disp == self.agc_var.get() else "black")
                    except:
                        self.agc_combo.config(foreground="black")

                nr_raw = self.rig_cmd("l NR")
                if nr_raw:
                    try:
                        raw_nr = float(nr_raw)
                        nr_level = round(raw_nr * 9)
                        self.nr_var.set(str(nr_level))
                        self.nr_combo.config(foreground="green" if nr_level == self.last_set.get("nr", 0) else "black")
                    except:
                        self.nr_combo.config(foreground="black")

                nb_raw = self.rig_cmd("l NB")
                if nb_raw:
                    try:
                        nb_level = int(float(nb_raw))
                        self.nb_var.set(str(nb_level))
                        self.nb_combo.config(foreground="green" if nb_level == self.last_set.get("nb", 0) else "black")
                    except:
                        self.nb_combo.config(foreground="black")

        except Exception as e:
            print(f"Poll error: {e}")

        elapsed = time.time() - start
        if elapsed > 0.3:
            self.status_var.set(f"Poll: {elapsed:.2f}s")
        else:
            self.status_var.set(f"Connected ✓ ({elapsed:.2f}s)")

        self.root.after(1000, self.update_readings)  # Poll every 1 second

    def reconnect(self):
        self.connect_to_rig()
        self.status_var.set("Reconnecting...")

    def quit_app(self):
        if self.sock: self.sock.close()
        self.root.destroy()

if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "localhost"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 4532
    app = FTX1MeterMonitor(host, port)
    app.root.mainloop()
