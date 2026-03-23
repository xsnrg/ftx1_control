#!/usr/bin/env python3
"""
FTX-1 Meter Monitor v1.3.2 - Hamlib Meters with debug logging
Only STRENGTH, RFPOWER, SWR, ALC, COMP (no more RFPOWER_METER/VD_METER/ID_METER)
Polling at 1s, send only on user change
"""

import tkinter as tk
from tkinter import ttk
import socket
import time
import logging
import argparse


class FTX1MeterMonitor:
    def __init__(self, host="localhost", port=4532, debug=False):
        # Setup logging
        self.logger = logging.getLogger("FTX1Meter")
        self.logger.setLevel(logging.DEBUG if debug else logging.WARNING)
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
        self.logger.addHandler(console_handler)

        self.host = host
        self.port = port
        self.sock = None

        self.root = tk.Tk()
        self.root.title("FTX-1 Meter Monitor v1.3.2")
        self.root.geometry("550x485")
        self.root.resizable(True, True)
        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)

        self.status_var = tk.StringVar(value="Initializing...")
        self.is_memory_mode = tk.BooleanVar(value=False)  # False = VFO mode
        self.freq_frame = None
        self.memory_channel_var = tk.StringVar(value="001")  # default or last used
        self.freq_var = tk.StringVar(value="—")

        # Standard Hamlib level tokens
        self.left_meters = {
            "STRENGTH": {
                "hamlib_cmd": "l STRENGTH",
                "scale": lambda r: r,
                "tx_only": False,
                "fmt": "{:.0f} dB",
                "max": 20
            },
            "PO": {
                "hamlib_cmd": "l RFPOWER_METER_WATTS",
                "scale": lambda r: r / 10,
                "tx_only": True,
                "fmt": "{:.1f} W",
                "max": 10
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
                "scale": lambda r: r * 10,
                "tx_only": True,
                "fmt": "{:.1f}",
                "max": 10.0
            },
            "COMP": {
                "hamlib_cmd": "l COMP",
                "scale": lambda r: r * 100,
                "tx_only": True,
                "fmt": "{:.0f}%",
                "max": 100
            },
            "VDD": {
                "hamlib_cmd": "l VD_METER",
                "scale": lambda r: r / 1.03,
                "tx_only": False,
                "fmt": "{:.1f} V",
                "max": 15.0
            },
            "ID": {
                "hamlib_cmd": "l ID_METER",
                "scale": lambda r: r / 10,
                "tx_only": True,
                "fmt": "{:.1f} A",
                "max": 4.0
            },
        }

        self.bw_options_by_mode = {
            "LSB": ["300", "400", "600", "850", "1100", "1200", "1500", "1650", "1800", "1950",
                    "2100", "2250", "2400", "2450", "2500", "2600", "2700", "2800", "2900",
                    "3000", "3200", "3500", "4000"],
            "USB": ["300", "400", "600", "850", "1100", "1200", "1500", "1650", "1800", "1950",
                    "2100", "2250", "2400", "2450", "2500", "2600", "2700", "2800", "2900",
                    "3000", "3200", "3500", "4000"],
            "CW-U": ["50", "100", "150", "200", "250", "300", "350", "400", "450", "500",
                     "600", "800", "1200", "1400", "1700", "2000", "2400", "3000", "3200",
                     "3500", "4000"],
            "CW-L": ["50", "100", "150", "200", "250", "300", "350", "400", "450", "500",
                     "600", "800", "1200", "1400", "1700", "2000", "2400", "3000", "3200",
                     "3500", "4000"],
            "RTTY": ["50", "100", "150", "200", "250", "300", "350", "400", "450", "500",
                     "600", "800", "1200", "1400", "1700", "2000", "2400", "3000", "3200",
                     "3500", "4000"],
            "RTTYR": ["50", "100", "150", "200", "250", "300", "350", "400", "450", "500",
                      "600", "800", "1200", "1400", "1700", "2000", "2400", "3000", "3200",
                      "3500", "4000"],
            "PKTUSB": ["50", "100", "150", "200", "250", "300", "350", "400", "450", "500",
                       "600", "800", "1200", "1400", "1700", "2000", "2400", "3000", "3200",
                       "3500", "4000"],
            "PKTLSB": ["50", "100", "150", "200", "250", "300", "350", "400", "450", "500",
                       "600", "800", "1200", "1400", "1700", "2000", "2400", "3000", "3200",
                       "3500", "4000"],
            "DATA-U": ["50", "100", "150", "200", "250", "300", "350", "400", "450", "500",
                       "600", "800", "1200", "1400", "1700", "2000", "2400", "3000", "3200",
                       "3500", "4000"],
            "DATA-L": ["50", "100", "150", "200", "250", "300", "350", "400", "450", "500",
                       "600", "800", "1200", "1400", "1700", "2000", "2400", "3000", "3200",
                       "3500", "4000"],
            "AM": ["9000"],
            "FM": ["16000"],
        }
        self.default_bw_options = ["50", "100", "150", "200", "250", "300", "400", "500", "600",
                                   "800", "1200", "1400", "1700", "2000", "2400", "3000", "3200",
                                   "3500", "4000"]
        self.power_options = [f"{x:.1f}" for x in [i * 0.5 for i in range(1, 21)]]  # 0.5, 1.0, ..., 10.0

        self.is_sub_vfo = tk.BooleanVar(value=False)  # False = Main, True = Sub
        self.vfo_status_label = None
        self.current_vfo = "Main"

        self.meter_labels = {}
        self.bar_canvases = {}
        self.status_label = None
        self.bar_height = 8

        self.power_var = tk.DoubleVar()
        self.preamp_var = tk.StringVar()
        self.att_var = tk.StringVar()
        self.sql_var = tk.DoubleVar()
        self.agc_var = tk.StringVar()
        self.nr_var = tk.StringVar()
        self.nb_var = tk.StringVar()
        self.mode_var = tk.StringVar()

        self.current_bw_str = "—"

        self.ignore_readback_until = 0.0
        self._poll_running = False

        self.startup_sync_done = False
        self.startup_retries = 0
        self.max_startup_retries = 5
        self.default_startup_bw = "0"
        self.last_mode_type = None  # Will be set from IF; poll (P7 byte)

        self.sync_in_progress = False
        self.last_user_change_time = time.time()
        self.last_control_sync_time = 0.0
        self.control_sync_interval = 10.0
        self.user_debounce_sec = 8.0

        self.build_gui()
        self.update_status_style(f"Connecting to {host}:{port}...", "gray")
        self.connect_to_rig()
        self.logger.info("Connect done — waiting for startup sync")

        # Initial UI state (safe default until first poll)
        self.v_m_btn.config(text="Mem")
        self.freq_entry.config(state="normal")

        # Start control sync and main polling
        self.root.after(2000, self._startup_control_sync)
        self.root.after(2500, self._update_vfo_status)
        self.root.after(1000, self.update_readings)  # polling starts immediately

    def build_gui(self):
        sf = ttk.LabelFrame(self.root, text="Radio Status")
        sf.pack(fill="x", padx=10, pady=5)

        self.freq_frame = ttk.Frame(sf)
        self.freq_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=3)

        ttk.Label(self.freq_frame, text="Freq:").pack(side="left")

        self.freq_entry = ttk.Entry(self.freq_frame, textvariable=self.freq_var,
                                    font=("Arial", 12, "bold"), width=14)
        self.freq_entry.pack(side="left", padx=(5, 8))

        self.main_sub_btn = ttk.Button(self.freq_frame, text="M/S", width=4,
                                       command=self.toggle_main_sub)
        self.main_sub_btn.pack(side="left", padx=(4, 2))

        self.vfo_status_label = ttk.Label(self.freq_frame, text="Main",
                                          font=("Arial", 10, "bold"),
                                          width=6, anchor="w")
        self.vfo_status_label.pack(side="left", padx=(2, 12))

        self.v_m_btn = ttk.Button(self.freq_frame, text="V/M", width=4,
                                  command=self.switch_vfo_memory)
        self.v_m_btn.pack(side="left", padx=(0, 5))

        # Memory channel selector (wider for 5 digits)
        self.memory_entry = ttk.Entry(
            self.freq_frame,
            textvariable=self.memory_channel_var,
            width=8,  # for "00002" or "50002"
            font=("Arial", 10),
            justify="center"
        )
        self.memory_entry.bind("<Return>", self.jump_to_memory_channel)

        self.memory_go_btn = ttk.Button(
            self.freq_frame,
            text="Go",
            width=5,
            command=self.jump_to_memory_channel
        )

        # Mode + Bandwidth row
        mode_frame = ttk.Frame(sf)
        mode_frame.grid(row=1, column=0, columnspan=2, sticky="w", padx=8, pady=3)

        ttk.Label(mode_frame, text="Mode:").pack(side="left")

        mode_combo = ttk.Combobox(mode_frame, textvariable=self.mode_var,
                                  values=["DATA-U", "DATA-L", "USB", "LSB", "CW-U", "CW-L", "AM", "FM",
                                          "RTTY", "RTTYR"], state="readonly", width=12)
        mode_combo.pack(side="left", padx=(5, 0))
        mode_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_controls())

        ttk.Label(mode_frame, text="Bandwidth:").pack(side="left", padx=(15, 5))

        self.bw_combo = ttk.Combobox(mode_frame, width=8, justify="right", font=("Arial", 10),
                                     state="readonly")
        self.bw_combo.pack(side="left", padx=(5, 0))
        self.bw_combo.bind("<<ComboboxSelected>>", self.set_bandwidth)

        ttk.Label(mode_frame, text="Hz").pack(side="left", padx=(2, 10))

        # Meters & Controls section – side-by-side layout
        msf = ttk.LabelFrame(self.root, text="Meters & Controls")
        msf.pack(fill="both", expand=True, padx=10, pady=6)

        msf.columnconfigure(0, weight=3)
        msf.columnconfigure(1, weight=0)
        msf.columnconfigure(2, weight=1)

        ttk.Separator(msf, orient='vertical').grid(row=0, column=1, sticky='ns', padx=4, pady=4)

        # Left side: meters
        left_meter_frame = ttk.Frame(msf)
        left_meter_frame.grid(row=0, column=0, sticky="nsew", padx=(10, 4), pady=6)

        left_meter_frame.columnconfigure(0, weight=0)
        left_meter_frame.columnconfigure(1, weight=1)

        ROW_PADY = 4
        LABEL_MINSIZE = 24
        BAR_MINSIZE = 16
        BAR_WIDTH = 180

        row = 0
        pretty_left = {
            "STRENGTH": "S-Meter",
            "PO": "PO",
            "SWR": "SWR",
            "ALC": "ALC",
            "COMP": "COMP",
            "VDD": "VDD",
            "ID": "ID",
        }

        for m in self.left_meters:
            label_text = pretty_left.get(m, m)

            left_meter_frame.rowconfigure(row, weight=0, minsize=LABEL_MINSIZE)

            ttk.Label(left_meter_frame, text=f"{label_text}:").grid(
                row=row, column=0, sticky="e", padx=(10, 4), pady=(ROW_PADY, 1)
            )

            var = tk.StringVar(value="—")
            self.meter_labels[m] = var
            ttk.Label(
                left_meter_frame,
                textvariable=var,
                font=("Arial", 11, "bold"),
                width=12,
                anchor="w"
            ).grid(row=row, column=1, sticky="w", padx=6, pady=(ROW_PADY, 1))

            left_meter_frame.rowconfigure(row + 1, weight=0, minsize=BAR_MINSIZE)

            canvas = tk.Canvas(
                left_meter_frame,
                width=BAR_WIDTH,
                height=self.bar_height,
                bg="#222",
                highlightthickness=0
            )
            canvas.grid(
                row=row + 1,
                column=1,
                sticky="w",
                padx=6,
                pady=(1, ROW_PADY + 2)
            )
            self.bar_canvases[m] = canvas

            row += 2

        # Right side: controls
        right_controls_frame = ttk.Frame(msf)
        right_controls_frame.grid(row=0, column=2, sticky="n", padx=(20, 10), pady=8)

        right_row = 0
        RIGHT_PADY = 8

        ttk.Label(right_controls_frame, text="Power (W):").grid(row=right_row, column=0, sticky="e", padx=(0, 8),
                                                                pady=RIGHT_PADY)
        self.power_combo = ttk.Combobox(right_controls_frame, textvariable=self.power_var,
                                        values=self.power_options, state="readonly", width=6)
        self.power_combo.grid(row=right_row, column=1, sticky="w", padx=4, pady=RIGHT_PADY)
        self.power_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_controls())
        right_row += 1

        ttk.Label(right_controls_frame, text="Preamp:").grid(row=right_row, column=0, sticky="e", padx=(0, 8),
                                                             pady=RIGHT_PADY)
        self.preamp_combo = ttk.Combobox(right_controls_frame, textvariable=self.preamp_var,
                                         values=["IPO", "AMP1", "AMP2"],
                                         state="readonly", width=8)
        self.preamp_combo.grid(row=right_row, column=1, sticky="w", padx=4, pady=RIGHT_PADY)
        self.preamp_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_controls())
        right_row += 1

        ttk.Label(right_controls_frame, text="ATT:").grid(row=right_row, column=0, sticky="e", padx=(0, 8),
                                                          pady=RIGHT_PADY)
        self.att_combo = ttk.Combobox(right_controls_frame, textvariable=self.att_var,
                                      values=["Off", "-6 dB", "-12 dB", "-18 dB"],
                                      state="readonly", width=8)
        self.att_combo.grid(row=right_row, column=1, sticky="w", padx=4, pady=RIGHT_PADY)
        self.att_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_controls())
        right_row += 1

        ttk.Label(right_controls_frame, text="Squelch:").grid(row=right_row, column=0, sticky="e", padx=(0, 8),
                                                              pady=RIGHT_PADY)
        self.sql_spin = tk.Spinbox(right_controls_frame, from_=0.0, to=1.0, increment=0.05,
                                   textvariable=self.sql_var, width=6,
                                   command=self.apply_controls)
        self.sql_spin.grid(row=right_row, column=1, sticky="w", padx=4, pady=RIGHT_PADY)
        right_row += 1

        ttk.Label(right_controls_frame, text="AGC:").grid(row=right_row, column=0, sticky="e", padx=(0, 8),
                                                          pady=RIGHT_PADY)
        self.agc_combo = ttk.Combobox(right_controls_frame, textvariable=self.agc_var,
                                      values=["Off", "Fast", "Medium", "Slow", "Auto"],
                                      state="readonly", width=10)
        self.agc_combo.grid(row=right_row, column=1, sticky="w", padx=4, pady=RIGHT_PADY)
        self.agc_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_controls())
        right_row += 1

        ttk.Label(right_controls_frame, text="Noise Red. (NR):").grid(row=right_row, column=0, sticky="e", padx=(0, 8),
                                                                      pady=RIGHT_PADY)
        self.nr_combo = ttk.Combobox(right_controls_frame, textvariable=self.nr_var,
                                     values=["Off", "1", "2", "3", "4", "5", "6", "7", "8", "9"],
                                     state="readonly", width=8)
        self.nr_combo.grid(row=right_row, column=1, sticky="w", padx=4, pady=RIGHT_PADY)
        self.nr_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_controls())
        right_row += 1

        ttk.Label(right_controls_frame, text="Noise Bl. (NB):").grid(row=right_row, column=0, sticky="e", padx=(0, 8),
                                                                     pady=RIGHT_PADY)
        self.nb_combo = ttk.Combobox(right_controls_frame, textvariable=self.nb_var,
                                     values=["Off", "1", "2", "3", "4", "5", "6", "7", "8", "9"],
                                     state="readonly", width=8)
        self.nb_combo.grid(row=right_row, column=1, sticky="w", padx=4, pady=RIGHT_PADY)
        self.nb_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_controls())

        # Bottom status bar
        bottom_frame = ttk.Frame(self.root)
        bottom_frame.pack(fill="x", pady=8, padx=10)

        # Status label — created here, ready for styling
        self.status_label = ttk.Label(bottom_frame, textvariable=self.status_var, font=("Arial", 9))
        self.status_label.pack(side="left")

        # Reconnect button
        reconnect_btn = ttk.Button(bottom_frame, text="Reconnect", command=self.reconnect)
        reconnect_btn.pack(side="right")

    def update_status_style(self, message, color="black", bold=False):
        if not self.status_label:
            return
        self.status_var.set(message)
        font_style = ("Arial", 9, "bold") if bold else ("Arial", 9)
        self.status_label.config(foreground=color, font=font_style)

    def _display_to_hamlib_mode(self, display_mode):
        """Map UI-displayed mode name to what Hamlib/radio expects."""
        map_display_to_hamlib = {
            "DATA-U": "PKTUSB",
            "DATA-L": "PKTLSB",
            # Add others only if needed; most are 1:1
        }
        return map_display_to_hamlib.get(display_mode, display_mode)

    def _hamlib_to_display_mode(self, hamlib_mode):
        """Map what radio/Hamlib returns to UI-displayed name."""
        map_hamlib_to_display = {
            "PKTUSB": "DATA-U",
            "PKTLSB": "DATA-L",
            # Add others if radio returns unexpected aliases
        }
        return map_hamlib_to_display.get(hamlib_mode, hamlib_mode)

    def _force_memory_refresh(self):
        """Called after V/M toggle or Next Mem — makes sure channel + freq appear instantly."""
        if not self.is_memory_mode.get():
            return

    def switch_vfo_memory(self):
        if not self.sock:
            self.status_var.set("Not connected")
            return

        resp = self.rig_cmd("w VM;", timeout=3.0)
        self.logger.info(f"Sent VM; toggle (response: {resp!r})")

        self.last_user_change_time = time.time()
        self.ignore_readback_until = time.time() + 3.0

        target_is_mem = not self.is_memory_mode.get()
        self.is_memory_mode.set(target_is_mem)

        if target_is_mem:
            self.v_m_btn.config(text="M → V")
            self.freq_entry.config(state="disabled")
            self.memory_entry.pack(side="left", padx=(5, 2))
            self.memory_go_btn.pack(side="left", padx=(0, 5))

            # Pre-fill with current channel
            _, current_ch = self.get_current_memory_channel()
            if current_ch is not None:
                self.memory_channel_var.set(f"{current_ch:05d}")
            else:
                self.memory_channel_var.set("00001")

        else:
            self.v_m_btn.config(text="V → M")
            self.freq_entry.config(state="normal")
            self.memory_entry.pack_forget()
            self.memory_go_btn.pack_forget()

        self.status_var.set(f"Switched to {'Memory' if target_is_mem else 'VFO'} mode")

    def toggle_main_sub(self):
        if not self.sock:
            self.status_var.set("Not connected")
            return

        target = "Sub" if self.current_vfo == "Main" else "Main"

        resp = self.rig_cmd(f"V {target}")
        if resp and "RPRT 0" in resp:
            self.logger.info(f"Switched to {target}")
            self.status_var.set(f"Switched to {target}")
        else:
            self.logger.warning(f"Switch to {target} failed: {resp}")
            self.status_var.set(f"Switch to {target} failed")

        # Delay for radio to settle, then refresh
        self.root.after(400, self._update_vfo_status)
        self.last_user_change_time = time.time()

    def _update_vfo_status(self):
        if not self.sock:
            self.vfo_status_label.config(text="?—")
            return

        resp = self.rig_cmd("v")
        if resp:
            vfo_str = resp.strip()
            if vfo_str in ["Main", "Sub"]:
                self.current_vfo = vfo_str
                self.vfo_status_label.config(
                    text=vfo_str,
                    foreground="green" if vfo_str == "Sub" else "blue"
                )
                self.logger.debug(f"Active VFO: {vfo_str}")
            else:
                self.vfo_status_label.config(text="?—")
                self.logger.warning(f"Unexpected vfo response: {resp}")
        else:
            self.vfo_status_label.config(text="?—")

    def next_memory(self):
        self.logger.debug("Sending next memory channel")
        resp = self.rig_cmd("w CH0;")
        if resp and "MC" in resp:
            self.logger.info("Next memory channel sent")
        else:
            self.logger.warning(f"Next memory may have failed: {resp}")

    def send_raw_cat(self, cat_str, expect_response=True, timeout=2.5):
        """
        Send raw Yaesu CAT command and attempt to read response.
        - expect_response=True for queries (MC;, IF;, etc.) — wait and return actual reply
        - expect_response=False for pure sets (VM;, SH00xx;, etc.) — return success on timeout
        Returns: actual response string (stripped) or None on failure / no response
        """
        if not cat_str.endswith(';'):
            cat_str += ';'
        cmd = f"w {cat_str.upper()}"

        try:
            self.logger.debug(f"Raw send: {cmd}")
            self.sock.sendall((cmd + "\n").encode('ascii'))

            if not expect_response:
                # Pure set: expect no reply or timeout is normal
                try:
                    resp = self._read_line(timeout=1.0)
                    self.logger.debug(f"Unexpected read on set: {resp!r}")
                except socket.timeout:
                    pass
                return "OK (set sent)"

            # Query: wait longer and return actual response
            self.sock.settimeout(timeout)
            try:
                resp = self._read_line(timeout=timeout)
                resp_clean = resp.strip() if resp else ""
                self.logger.debug(f"Raw read success: {resp_clean!r}")
                return resp_clean
            except socket.timeout:
                self.logger.debug("Timeout waiting for response on query")
                return None
            finally:
                self.sock.settimeout(1.0)

        except Exception as e:
            self.logger.error(f"Raw CAT error: {e}")
            self.sock = None
            return None

    def update_bw_combo_options(self):
        mode = self.mode_var.get().strip()
        if mode in ["—", ""]:
            self.bw_combo['values'] = self.default_bw_options
            return

        key_mode = mode
        if "PKT" in mode:
            key_mode = "PKTUSB"
        elif "RTTY" in mode:
            key_mode = "RTTY"

        opts = self.bw_options_by_mode.get(key_mode, self.default_bw_options)
        self.bw_combo['values'] = opts

    def update_meter_gui(self, m, value):
        var = self.meter_labels[m]
        canvas = self.bar_canvases[m]
        cfg = self.left_meters[m]

        # Format display value
        disp = cfg["fmt"].format(value)
        var.set(disp)

        canvas.delete("all")

        fill_color = "gray"

        # Special color logic for VDD meter
        if m == "VDD":
            # Normalize VDD: 10.0 = 0%, 15.0 = 100%
            norm_value = max(0.0, min(1.0, (value - 10.0) / (15.0 - 10.0)))
            fill_width = int(100 * norm_value)
            if 12.0 <= value <= 14.4:
                fill_color = "green"  # good range
            elif (value < 12.0 and value >= 11.5) or (value > 14.4 and value <= 14.8):
                fill_color = "orange"  # caution (yellow-ish)
            else:
                fill_color = "red"  # critical (<11.5 or >14.8)
        else:
            # Existing logic for other meters
            fill_width_pct = min(value / cfg["max"], 1.0)
            if fill_width_pct < 0.8:
                fill_color = "green"
            elif fill_width_pct < 1.0:
                fill_color = "orange"
            else:
                fill_color = "red"

        # Draw background
        canvas.create_rectangle(0, 0, 100, self.bar_height, fill="#333", outline="")

        # Draw filled bar
        fill_width = int(100 * min(value / cfg["max"], 1.0))
        canvas.create_rectangle(0, 0, fill_width, self.bar_height, fill=fill_color, outline="")

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
                self.logger.debug(f"Invalid response for try_set: {resp}")
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
                self.logger.debug(f"Successfully set value: {val}")
            except (ValueError, TypeError) as e:
                self.logger.debug(f"Parse/set failed: {e} (raw={resp})")

        # RFPOWER: 0.0-1.0 → watts (clamp 0.5-10)
        resp = self.rig_cmd("l RFPOWER")
        self.logger.debug(f"l RFPOWER → {resp}")
        if resp:
            try:
                raw = float(resp.strip())
                watts = max(0.5, min(10.0, raw * 10))  # no rounding
                self.power_var.set(f"{watts:.1f}")
                self.logger.debug(f"Power readback: raw {raw:.2f} → displayed {watts:.1f}")
            except:
                self.power_var.set("0.5")
        else:
            self.power_var.set("0.5")

            # PREAMP: Hamlib returns 0=IPO, 10=AMP1, 20=AMP2
            resp = self.rig_cmd("l PREAMP")
            self.logger.debug(f"l PREAMP → {resp}")

            def set_preamp(v):
                val = int(round(v))
                if val == 0:
                    self.preamp_var.set("IPO")
                elif val == 10:
                    self.preamp_var.set("AMP1")
                elif val == 20:
                    self.preamp_var.set("AMP2")
                else:
                    self.preamp_var.set("IPO")

            try_set(None, resp, parser=float, setter=set_preamp)

        # ATT: 0=Off, 6=-6, 12=-12, 18=-18
        resp = self.rig_cmd("l ATT")
        self.logger.debug(f"l ATT → {resp}")

        def set_att(v):
            map_ = {0: "Off", 6: "-6 dB", 12: "-12 dB", 18: "-18 dB"}
            self.att_var.set(map_.get(int(round(v)), "Off"))

        try_set(None, resp, parser=float, setter=set_att)

        # SQL: 0.0-1.0 float
        resp = self.rig_cmd("l SQL")
        self.logger.debug(f"l SQL → {resp}")
        try_set(self.sql_var, resp, parser=float)

        # AGC: 0=Off,1=Fast,2=Medium,3=Slow,4=Auto (confirm with your rig)
        resp = self.rig_cmd("l AGC")
        self.logger.debug(f"l AGC → {resp}")

        def set_agc(v):
            map_ = {0: "Off", 1: "Fast", 2: "Medium", 3: "Slow", 4: "Auto"}
            self.agc_var.set(map_.get(int(round(v)), "Off"))

        try_set(None, resp, parser=float, setter=set_agc)

        # NR (DNR): 0.0-1.0 → map to 0="Off", 1-10
        resp = self.rig_cmd("l NR")
        self.logger.debug(f"l NR → {resp}")

        def set_nr(raw):
            level = int(round(raw * 10))  # 0.0→0, 0.0667→1, up to 1.0→10
            nr_str = "Off" if level <= 0 else str(min(10, max(0, level)))
            self.nr_var.set(nr_str)

        try_set(None, resp, parser=float, setter=set_nr)

        # NB: assume 0.0-1.0 normalized → scale to int 0-10
        resp = self.rig_cmd("l NB")
        self.logger.debug(f"l NB → {resp}")

        def set_nb(raw):
            level = int(round(raw * 10))  # common mapping
            nb_str = "Off" if level <= 0 else str(min(10, max(0, level)))
            self.nb_var.set(nb_str)

        try_set(None, resp, parser=float, setter=set_nb)

        self.sync_in_progress = False
        self.last_control_sync_time = now

        self.logger.info(f"Control sync: {success}/7 successful")
        return success

    def _startup_control_sync(self):
        if self.startup_sync_done:
            return

        success = self._perform_control_sync(force=True)

        if success >= 5:
            self.startup_sync_done = True
            self.status_var.set("Startup sync OK ✓")

            # Initial bandwidth sync — give it more time + retry once
            self.logger.info("Performing initial bandwidth sync via SH0;")
            mode = self.mode_var.get().strip().upper() or "USB"
            sh_resp = None
            for attempt in range(2):  # retry once
                sh_resp = self.rig_cmd("w SH0;", timeout=10.0)
                if sh_resp and "SH" in sh_resp:
                    break
                self.logger.warning(f"Startup SH0 attempt {attempt + 1} timed out - retrying")
                time.sleep(0.5)

            if sh_resp and "SH" in sh_resp:
                try:
                    idx_part = sh_resp.rstrip(";").split("SH")[1].strip().lstrip("0")
                    idx = int(idx_part) if idx_part else 0
                    self.logger.debug(f"Startup SH0 response: {sh_resp} (index {idx})")

                    key_mode = mode
                    if "PKT" in key_mode or "DATA" in key_mode:
                        key_mode = "PKTUSB"
                    elif "RTTY" in key_mode:
                        key_mode = "RTTY"
                    elif "CW" in key_mode:
                        key_mode = "CW-U"
                    elif key_mode in ("USB", "LSB"):
                        key_mode = "USB"

                    options = self.bw_options_by_mode.get(key_mode, self.default_bw_options)

                    if 1 <= idx <= len(options):
                        real_bw = options[idx - 1]
                        self.current_bw_str = real_bw
                        self.bw_combo.set(real_bw)
                        self.logger.info(f"Startup BW synced: {real_bw} Hz (index {idx})")
                    else:
                        self.logger.warning(f"Startup BW index out of range: {idx} - using default")
                        self.current_bw_str = self.default_startup_bw
                        self.bw_combo.set(self.default_startup_bw)
                except Exception as ex:
                    self.logger.error(f"Startup SH0 parse failed: {ex}")
                    self.current_bw_str = self.default_startup_bw
                    self.bw_combo.set(self.default_startup_bw)
            else:
                self.logger.warning("Startup SH0 failed after retry - using default")
                self.current_bw_str = self.default_startup_bw
                self.bw_combo.set(self.default_startup_bw)

        else:
            self.startup_retries += 1
            if self.startup_retries < self.max_startup_retries:
                self.root.after(4000, self._startup_control_sync)
            else:
                self.startup_sync_done = True

    def set_frequency(self):
        if self.is_memory_mode.get():
            return  # ignore in Memory mode
        try:
            freq_str = self.freq_var.get().strip()
            freq_hz = float(freq_str) * 1000000  # assume MHz input
            self.rig_cmd(f"F {int(freq_hz)}")
            self.logger.info(f"Frequency set to {freq_hz / 1e6:.6f} MHz")
        except ValueError:
            self.status_var.set("Invalid frequency — keeping previous")

    def apply_controls(self):
        if not self.sock:
            self.status_var.set("Not connected")
            return

        self.last_user_change_time = time.time()
        self.ignore_readback_until = time.time() + 12.0
        self.freq_entry.bind("<Return>", lambda e: self.set_frequency())

        # Power handling (safe ignore on invalid)
        power_w = self.power_var.get()  # already a float from DoubleVar
        power_valid = True

        # Validate range (no need for try/except on float conversion)
        if not (0.5 <= power_w <= 10.0):
            self.status_var.set(f"Power out of range ({power_w:.1f} W) — keeping previous")
            self.logger.warning(f"Power out of range: {power_w:.1f} W - ignoring")
            power_valid = False

        if power_valid:
            power_raw = power_w / 10.0
            self.rig_cmd(f"L RFPOWER {power_raw:.2f}")
            self.status_var.set(f"Power set to {power_w:.1f} W")

        preamp_str = self.preamp_var.get()
        preamp_map = {"IPO": 0, "AMP1": 10, "AMP2": 20}
        preamp_val = preamp_map.get(preamp_str, 0)
        self.rig_cmd(f"L PREAMP {preamp_val}")
        self.logger.debug(f"PREAMP set via Hamlib → {preamp_str} (level {preamp_val})")

        # Other controls (always apply)
        sql_val = self.sql_var.get()
        self.rig_cmd(f"L SQL {sql_val:.2f}")

        agc_map = {"Off": 0, "Fast": 1, "Medium": 2, "Slow": 3, "Auto": 6}
        agc_val = agc_map.get(self.agc_var.get(), 0)
        self.rig_cmd(f"L AGC {agc_val}")

        # NR
        nr_display = self.nr_var.get()
        if nr_display == "Off":
            nr_int = 0
        else:
            try:
                nr_int = int(nr_display)
                if not 1 <= nr_int <= 10:
                    nr_int = 0
            except ValueError:
                nr_int = 0

        nr_normalized = nr_int / 10.0
        self.rig_cmd(f"L NR {nr_normalized:.4f}")

        # NB
        nb_display = self.nb_var.get()
        nb_val = 0 if nb_display == "Off" else int(nb_display)
        self.rig_cmd(f"L NB {nb_val}")

        # Mode
        mode_str = self.mode_var.get().strip()
        if mode_str and mode_str != "—":
            hamlib_mode = self._display_to_hamlib_mode(mode_str)
            self.rig_cmd(f"M {hamlib_mode} 0")
            self.logger.debug(f"Mode set: displayed '{mode_str}' → sent '{hamlib_mode}'")

        # Final status (after all changes)
        if power_valid:
            self.update_status_style("Changes applied", "#006600", bold=True)
            self.root.after(4000, lambda: self.update_status_style("Connected ✓", "#00CC00", bold=True))
        else:
            self.status_var.set("Changes applied (power unchanged)")

    def set_bandwidth(self, event=None):
        bw_str = self.bw_combo.get().strip()
        if not bw_str or bw_str == "—":
            return

        mode = self.mode_var.get().strip().upper()
        if not mode or mode == "—":
            self.status_var.set("Select mode first")
            return

        try:
            bw = int(bw_str)

            key_mode = mode
            if "PKT" in mode or "DATA" in mode:
                key_mode = "PKTUSB"
            elif "RTTY" in mode:
                key_mode = "RTTY"
            elif "CW" in mode:
                key_mode = "CW-U"
            elif mode in ("USB", "LSB"):
                key_mode = "USB"

            options = self.bw_options_by_mode.get(key_mode, self.default_bw_options)

            if bw_str not in options:
                self.status_var.set(f"{bw} Hz invalid for {mode}")
                self.logger.warning(f"Invalid BW for mode {mode}: {bw}")
                return

            idx = options.index(bw_str) + 1
            p3 = f"{idx:02d}"
            raw_cmd = f"SH00{p3}"

            resp = self.send_raw_cat(raw_cmd)
            self.logger.debug(f"Set BW: {raw_cmd} → {resp or 'sent (no reply)'}")

            if resp is None or "Error" in str(resp):
                self.status_var.set(f"Failed to set {bw} Hz ({raw_cmd})")
                self.logger.warning(f"BW set may have failed: {resp}")
            else:
                self.current_bw_str = bw_str
                self.bw_combo.set(bw_str)
                self.status_var.set(f"Bandwidth set to {bw} Hz ({raw_cmd})")
                self.logger.info(f"BW updated in UI: {bw_str} Hz")

            self.last_user_change_time = time.time()
            self.ignore_readback_until = time.time() + 8.0

        except Exception as e:
            self.status_var.set(f"Error setting BW: {e}")
            self.logger.error(f"set_bandwidth exception: {e}")

    def connect_to_rig(self):
        try:
            if self.sock:
                self.sock.close()
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(5.0)
            self.sock.connect((self.host, self.port))
            self.update_status_style("Connected ✓", "#00CC00", bold=True)
            self.logger.info("Connected to rigctld")
            return True
        except Exception as e:
            self.status_var.set(f"Connect error: {e}")
            self.logger.error(f"Connect failed: {e}")
            self.sock = None
            return False

    def _read_line(self, timeout=3.0):
        """Read until ; or \n — Yaesu CAT uses ; as real terminator."""
        self.sock.settimeout(timeout)
        buf = bytearray()
        start_time = time.time()
        try:
            while time.time() - start_time < timeout:
                try:
                    chunk = self.sock.recv(4096)
                    if not chunk:
                        raise socket.timeout("Connection closed by remote")
                    buf.extend(chunk)

                    # Check for Yaesu-style terminator: ;
                    if b';' in buf:
                        # Split at first ;
                        parts = buf.split(b';', 1)
                        line_bytes = parts[0]
                        rest = parts[1] if len(parts) > 1 else b''
                        decoded = line_bytes.decode('ascii', errors='ignore').strip()
                        self.logger.debug(f"Read Yaesu response (ended by ;): {decoded};")
                        # Optionally put rest back or drain later
                        return decoded + ";"  # include ; so caller sees full response

                    # Fallback to \n if no ; found
                    if b'\n' in buf:
                        line_bytes, rest = buf.split(b'\n', 1)
                        decoded = line_bytes.decode('ascii', errors='ignore').strip()
                        self.logger.debug(f"Read line (ended by \\n): {decoded}")
                        return decoded

                except BlockingIOError:
                    time.sleep(0.01)

            raise socket.timeout("Read timeout - no ; or \\n received")

        except socket.timeout:
            self.logger.debug("Read timeout in _read_line")
            raise
        except Exception as e:
            self.logger.error(f"Read error: {e}")
            raise

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
        return drained

    def rig_cmd(self, cmd, timeout=4.0):
        if not self.sock:
            return None
        try:
            self.logger.debug(f"Sending: {cmd}")
            self.sock.sendall((cmd + "\n").encode('ascii'))
            resp = self._read_line(timeout=timeout)
            self.logger.debug(f"Received: {resp}")
            if not resp:
                return None
            if "RPRT" in resp:
                try:
                    code = int(resp.split("RPRT")[1].strip())
                    if code != 0:
                        self.logger.warning(f"RPRT error {code} for {cmd}")
                        return None
                except:
                    pass
            return resp
        except socket.timeout:
            self.logger.debug(f"Timeout on {cmd}")
            return None  # don't kill socket on timeout
        except Exception as e:
            self.logger.error(f"rig_cmd failed '{cmd}': {e}")
            self.sock = None
            self.update_status_style("Disconnected — reconnecting...", "red")
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

    def get_current_memory_channel(self):
        if not self.sock:
            return None, None

        side_prefix = "1" if self.current_vfo == "Sub" else "0"
        cmd = f"MC{side_prefix};"

        resp = self.send_raw_cat(cmd, expect_response=True, timeout=3.0)

        if not resp or "OK" in resp or resp.strip() in ["?;", "MC;", "MC0;", "MC1;"]:
            self.logger.warning(f"MC read failed or invalid: {resp!r}")
            return None, None

        resp_clean = resp.strip().rstrip(";")
        if resp_clean.startswith("MC") and len(resp_clean) >= 7:
            param = resp_clean[2:]
            if len(param) == 6 and param.isdigit():
                side = int(param[0])
                ch_num = int(param[1:])
                self.logger.info(f"MC parsed: side={side} ({'Main' if side == 0 else 'Sub'}), ch={ch_num:05d}")
                return side, ch_num

        self.logger.warning(f"Unexpected MC format: {resp_clean!r}")
        return None, None

    def jump_to_memory_channel(self, event=None):
        if not self.sock or not self.is_memory_mode.get():
            self.status_var.set("Not in memory mode or disconnected")
            return

        ch_str = self.memory_channel_var.get().strip()
        if not ch_str.isdigit() or len(ch_str) > 5 or len(ch_str) < 1:
            self.status_var.set("Enter 1–5 digits (e.g. 23, 2, 50002)")
            return

        ch_num = int(ch_str)
        if ch_num == 0 or ch_num > 99999:
            self.status_var.set("Channel out of range")
            return

        side_prefix = "1" if self.current_vfo == "Sub" else "0"
        cmd_ch = f"{ch_num:05d}"
        full_cmd = f"MC{side_prefix}{cmd_ch};"

        self.logger.info(f"Sending raw: {full_cmd}")
        resp = self.send_raw_cat(full_cmd, expect_response=False, timeout=1.5)
        self.logger.debug(f"Set raw response (expected empty or OK): {resp!r}")

        time.sleep(0.8)

        new_side, new_ch = self.get_current_memory_channel()
        if new_ch == ch_num:
            self.status_var.set(f"Switched to channel {new_ch:05d} (side {'Main' if new_side==0 else 'Sub'})")
            self.logger.info(f"Success → channel {new_ch:05d}")

            # ── Inline forced refresh after successful switch ──
            def _refresh_after_jump():
                self.logger.info("Forced control + bandwidth refresh after memory channel jump")

                # Full control sync (power, preamp, ATT, AGC, NR, NB, etc.)
                self._perform_control_sync(force=True)

                # Bandwidth sync (query SH0; and update combo)
                mode = self.mode_var.get().strip().upper() or "USB"
                sh_resp = self.rig_cmd("w SH0;", timeout=4.0)
                if sh_resp and "SH" in sh_resp:
                    try:
                        idx_part = sh_resp.rstrip(";").split("SH")[1].strip().lstrip("0")
                        idx = int(idx_part) if idx_part else 0
                        self.logger.debug(f"SH0 after jump: {sh_resp} (index {idx})")

                        key_mode = mode
                        if "PKT" in key_mode or "DATA" in key_mode:
                            key_mode = "PKTUSB"
                        elif "RTTY" in key_mode:
                            key_mode = "RTTY"
                        elif "CW" in key_mode:
                            key_mode = "CW-U"
                        elif mode in ("USB", "LSB"):
                            key_mode = "USB"

                        options = self.bw_options_by_mode.get(key_mode, self.default_bw_options)

                        if 1 <= idx <= len(options):
                            real_bw = options[idx - 1]
                            self.current_bw_str = real_bw
                            self.bw_combo.set(real_bw)
                            self.logger.info(f"BW refreshed after jump: {real_bw} Hz (index {idx})")
                        else:
                            self.logger.warning(f"BW index {idx} out of range")
                            self.bw_combo.set("—")
                    except Exception as ex:
                        self.logger.error(f"SH0 parse failed after jump: {ex}")
                        self.bw_combo.set("—")
                else:
                    self.logger.warning("No SH0 response after jump")
                    self.bw_combo.set("—")

                # Ensure combo options are fresh for current mode
                self.update_bw_combo_options()

        else:
            self.status_var.set(f"Selected {ch_num:05d} (confirmation unavailable)")
            self.logger.warning("No confirmation after set (normal with raw CAT)")

        self.last_user_change_time = time.time()
        self.ignore_readback_until = time.time() + 4.0

    def update_readings(self):
        if hasattr(self, '_poll_running') and self._poll_running:
            self.logger.debug("Poll already active - skipping duplicate")
            return
        self._poll_running = True

        now = time.time()
        if now - self.last_user_change_time > 1.5:
            self._update_vfo_status()

        if now - self.last_control_sync_time > self.control_sync_interval:
            if time.time() >= self.ignore_readback_until:
                self._perform_control_sync()

        if not self.sock:
            self.update_status_style("Disconnected — reconnecting...", "red")
            if self.reconnect():
                self.status_var.set("Reconnected ✓")
            else:
                self.root.after(2000, self.update_readings)
                return

        try:
            self._drain_socket()  # clear junk

            # Limit read attempts to prevent hang
            if_resp = self.rig_cmd("w IF;", timeout=3.0)
            if not if_resp:
                raise RuntimeError("IF; no response")

            if if_resp.startswith("IF") and len(if_resp) >= 30:
                # Parse IF (unchanged)
                something    = if_resp[2:7]
                freq_raw     = if_resp[7:16]
                clarifier    = if_resp[16:21]
                clarifier_rx = if_resp[21:22]
                clarifier_tx = if_resp[22:23]
                mode_code    = if_resp[23:24]
                mode_type    = if_resp[24:25]
                encoding     = if_resp[26:27]
                p9           = if_resp[27:29]
                simplex      = if_resp[29:30]

                self.last_mode_type = mode_type

                try:
                    freq_hz = int(freq_raw.strip())
                    freq_mhz = freq_hz / 1_000_000.0
                    freq_str = f"{freq_mhz:.6f} MHz"
                except:
                    freq_str = "—"
                    self.logger.warning(f"Freq parse failed: raw={freq_raw!r}")

                # Mode (P6 = 1 byte)
                mode_map = {
                    "1": "LSB", "2": "USB", "3": "CW-U", "4": "FM", "5": "AM",
                    "6": "RTTY-L", "7": "CW-L", "8": "DATA-L", "9": "RTTY-U",
                    "A": "DATA-FM", "B": "FM-N", "C": "DATA-U", "D": "AM-N",
                    "E": "PSK", "F": "DATA-FM-N", "H": "C4FM-DN", "I": "C4FM-VW"
                }
                display_mode = mode_map.get(mode_code, f"Mode {mode_code}")
                self.mode_var.set(display_mode)
                self.update_bw_combo_options()

                is_memory = mode_type in ["1", "2", "3", "5"]

                if time.time() >= self.ignore_readback_until and is_memory != self.is_memory_mode.get():
                    self.is_memory_mode.set(is_memory)
                    if is_memory:
                        self.v_m_btn.config(text="M → V")
                        self.freq_entry.config(state="disabled")
                        self.memory_entry.pack(side="left", padx=(5, 2))
                        self.memory_go_btn.pack(side="left", padx=(0, 5))

                        _, ch_num = self.get_current_memory_channel()
                        if ch_num is not None:
                            self.memory_channel_var.set(f"{ch_num:05d}")
                        else:
                            self.memory_channel_var.set("00001")
                    else:
                        self.v_m_btn.config(text="V → M")
                        self.freq_entry.config(state="normal")
                        self.memory_entry.pack_forget()
                        self.memory_go_btn.pack_forget()

                    self.logger.info(f"Poll detected mode change → {'Memory' if is_memory else 'VFO'} (P7={mode_type})")

                display_prefix = ""
                if is_memory:
                    side, ch_num = self.get_current_memory_channel()
                    if ch_num is not None:
                        side_str = "Main" if side == 0 else "Sub"
                        if 50000 <= ch_num <= 50200:
                            preset_id = ch_num - 50000 + 1
                            display_prefix = f"5 MHz Preset {preset_id:02d} ({side_str})  "
                        else:
                            display_prefix = f"CH {ch_num:05d} ({side_str})  "
                    else:
                        display_prefix = "Memory (?)  "

                self.freq_var.set(display_prefix + freq_str)

            else:
                self.freq_var.set("IF poll failed")

            for name, cfg in self.left_meters.items():
                try:
                    raw_str = self.get_hamlib_level(cfg["hamlib_cmd"])
                    if raw_str is None:
                        self.update_meter_gui(name, 0.0)
                        continue
                    raw = float(raw_str)
                    value = cfg["scale"](raw)
                    if cfg.get("tx_only", False) and value <= 0.0:
                        value = 0.0
                    self.update_meter_gui(name, value)
                except Exception as meter_e:
                    self.logger.debug(f"Meter {name} update failed: {meter_e}")
                    self.update_meter_gui(name, 0.0)

            # Always schedule next poll (critical!)
            self.root.after(1000, self.update_readings)
            self._poll_running = False

        except Exception as e:
            self.logger.error(f"Polling loop crashed: {e}", exc_info=True)
            self.sock = None  # force reconnect next time
            self.freq_var.set("Polling Err - retrying")
            self.update_status_style("Polling error - reconnecting...", "red")
            self.root.after(2000, self.update_readings)  # retry sooner

        self.update_status_style("Connected ✓", "#00CC00", bold=True)

    def reconnect(self):
        self.update_status_style("Disconnected — reconnecting...", "red")
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
        self.sock = None
        return self.connect_to_rig()

    def quit_app(self):
        if hasattr(self, 'sock') and self.sock:
            try:
                self.sock.close()
            except:
                pass
        self.root.destroy()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FTX-1 Meter Monitor")
    parser.add_argument("--host", default="localhost", help="rigctld host")
    parser.add_argument("--port", type=int, default=4532, help="rigctld port")
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    app = FTX1MeterMonitor(host=args.host, port=args.port, debug=args.debug)
    app.root.mainloop()
