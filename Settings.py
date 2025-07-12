import tkinter as tk
from tkinter import ttk, messagebox
import json
import os
import socket
import struct
import time
from decimal import Decimal, getcontext
from screeninfo import get_monitors
from pythonosc.osc_message_builder import OscMessageBuilder

CONFIG_FILE = "config.json"
X32_IP = "192.168.3.110"
X32_PORT = 10023
LOCAL_PORT = 10025
SUBSCRIPTION_NAME = "mtrs"
METERS_PATH = "/meters/1"
COLLECTION_DURATION = 3
getcontext().prec = 12

# Load config or use defaults
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    else:
        return {
            "FULLSCREEN_MODE": True,
            "X32_IP": "192.168.3.110",
            "X32_PORT": 10023,
            "LOCAL_PORT": 10024,
            "SUBSCRIPTION_NAME": "mtrs",
            "METERS_PATH": "/meters/1",
            "RENEW_INTERVAL": 9,
            "POLL_SEC": 0.05,
            "OBS_HOST": "LBC-AV1.local",
            "OBS_PORT": 4455,
            "OBS_PASSWORD": "161616",
            "GROUP_CHANNELS": {
                "Instrumental": [3, 4, 5],
                "Handheld": [9, 10, 11, 12],
                "Choir": [13, 14, 15, 16]
            },
            "INDIVIDUAL_CHANNELS": [6, 7, 8],
            "DCAS": [6, 7, 8],
            "THRESHOLDS": {
                "3": 0.0000165134,
                "4": 0.0000148019,
                "5": 0.0000151569,
                "6": 0.0000190000,
                "7": 0.0000421634,
                "8": 0.0000489722,
                "9": 0.0000536250,
                "10": 0.0000735157,
                "11": 0.0000388628,
                "12": 0.0000366700,
                "13": 0.0000306327,
                "14": 0.0000243578,
                "15": 0.0000200554,
                "16": 0.0000227090
            },
            "DISPLAY_INDEX": 1
        }

# Save config
def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    root.destroy()

config = load_config()

monitor = get_monitors()[0]
root = tk.Tk()
root.title("Solentra Configuration")
root.geometry(f"900x800+{monitor.x + 100}+{monitor.y + 100}")

notebook = ttk.Notebook(root)
notebook.pack(fill='both', expand=True, padx=10, pady=10)

main_frame = ttk.Frame(notebook)
thresh_frame = ttk.Frame(notebook)

notebook.add(main_frame, text='General')
notebook.add(thresh_frame, text='Thresholds')

entries = {}

def add_field(parent, label_text, key):
    ttk.Label(parent, text=label_text).pack(anchor='w')
    var = tk.StringVar(value=str(config.get(key, "")))
    entry = ttk.Entry(parent, textvariable=var)
    entry.pack(fill='x')
    entries[key] = var

for key, label in [
    ("X32_IP", "X32 IP Address"),
    ("X32_PORT", "X32 Port"),
    ("LOCAL_PORT", "Local Port"),
    ("OBS_HOST", "OBS Hostname"),
    ("OBS_PORT", "OBS Port"),
    ("OBS_PASSWORD", "OBS Password"),
    ("METERS_PATH", "Meter Path"),
    ("SUBSCRIPTION_NAME", "Subscription Name"),
    ("RENEW_INTERVAL", "Renew Interval (sec)"),
    ("POLL_SEC", "Poll Interval (sec)"),
    ("DISPLAY_INDEX", "Display Index")
]:
    add_field(main_frame, label, key)

fullscreen_var = tk.BooleanVar(value=config.get("FULLSCREEN_MODE", True))
ttkn = ttk.Checkbutton(main_frame, text="Fullscreen Mode", variable=fullscreen_var)
ttkn.pack(anchor='w', pady=(10, 10))

threshold_vars = {}
threshold_checks = {}

tt_thresh_scroll = tk.Canvas(thresh_frame)
thresh_scrollbar = ttk.Scrollbar(thresh_frame, orient="vertical", command=tt_thresh_scroll.yview)
thresh_container = ttk.Frame(tt_thresh_scroll)

thresh_container.bind("<Configure>", lambda e: tt_thresh_scroll.configure(scrollregion=tt_thresh_scroll.bbox("all")))
tt_thresh_scroll.create_window((0, 0), window=thresh_container, anchor="nw")
tt_thresh_scroll.configure(yscrollcommand=thresh_scrollbar.set)
tt_thresh_scroll.pack(side="left", fill="both", expand=True)
thresh_scrollbar.pack(side="right", fill="y")

select_all_var = tk.BooleanVar()

def toggle_all():
    for var in threshold_checks.values():
        var.set(select_all_var.get())

ttk.Checkbutton(thresh_container, text="Select All", variable=select_all_var, command=toggle_all).pack(anchor='w')

for k, v in config["THRESHOLDS"].items():
    frame = ttk.Frame(thresh_container)
    frame.pack(fill='x')
    chk_var = tk.BooleanVar()
    threshold_checks[k] = chk_var
    ttk.Checkbutton(frame, variable=chk_var).pack(side='left')
    ttk.Label(frame, text=f"Channel {k}").pack(side='left', padx=5)
    var = tk.StringVar(value=str(v))
    threshold_vars[k] = var
    entry = ttk.Entry(frame, textvariable=var, width=20)
    entry.pack(side='left', fill='x', expand=True)

def send_osc_message(sock, address, types, args):
    builder = OscMessageBuilder(address=address)
    for t, a in zip(types, args):
        builder.add_arg(a, t)
    sock.sendto(builder.build().dgram, (X32_IP, X32_PORT))

def parse_x32_meter_blob(data):
    header_length = 12
    blob = data[header_length:]
    num_values = struct.unpack('<I', blob[4:8])[0]
    float_data = blob[8:]
    values = struct.unpack('<' + 'f' * num_values, float_data[:num_values * 4])
    return [float(Decimal(str(v)).quantize(Decimal('0.0000000001'))) for v in values]

def collect_levels(state, selected_channels):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', LOCAL_PORT))
    send_osc_message(sock, '/batchsubscribe', 'ssiii', [SUBSCRIPTION_NAME, METERS_PATH, 0, 0, 0])
    buffer = {ch: [] for ch in selected_channels}
    print(f"Collecting values with mics {state}...")

    end_time = time.time() + COLLECTION_DURATION
    sock.settimeout(0.5)

    while time.time() < end_time:
        try:
            data, _ = sock.recvfrom(4096)
            if len(data) > 225:
                values = parse_x32_meter_blob(data)
                for ch in selected_channels:
                    if ch - 1 < len(values):
                        buffer[ch].append(values[ch - 1])
        except socket.timeout:
            continue

    sock.close()
    return {
        ch: max(buffer[ch]) if state == "off" else min(buffer[ch]) if buffer[ch] else 0.0
        for ch in selected_channels
    }

def generate_thresholds(mins, maxs):
    thresholds = {}
    for ch in mins:
        low = Decimal(str(min(mins[ch], maxs[ch])))
        high = Decimal(str(max(mins[ch], maxs[ch])))
        mid = low + (high - low) / 2
        thresholds[str(ch)] = float(mid.quantize(Decimal('0.0000000001')))
    return thresholds

def set_thresholds():
    selected_channels = [int(k) for k, v in threshold_checks.items() if v.get()]
    if not selected_channels:
        messagebox.showwarning("No Channels Selected", "Please select at least one channel.")
        return
    messagebox.showinfo("Step 1", "Unplug/turn off all microphones, then click OK to begin max level capture.")
    max_levels = collect_levels("off", selected_channels)
    messagebox.showinfo("Step 2", "Plug in/turn on all microphones, then click OK to begin min level capture.")
    min_levels = collect_levels("on", selected_channels)
    thresholds = generate_thresholds(min_levels, max_levels)
    for ch, val in thresholds.items():
        config["THRESHOLDS"][ch] = val
        threshold_vars[ch].set(str(val))
    save_config(config)

# Buttons
btn_frame = ttk.Frame(root)
btn_frame.pack(pady=10)

ttk.Button(btn_frame, text="Save Configuration", command=lambda: on_save()).pack(side="left", padx=5)

ttk.Button(thresh_container, text="Set Thresholds", command=set_thresholds).pack(anchor='w', pady=10)

def on_save():
    for key, var in entries.items():
        val = var.get()
        try:
            if key in ["X32_PORT", "LOCAL_PORT", "OBS_PORT", "DISPLAY_INDEX"]:
                config[key] = int(val)
            elif key in ["RENEW_INTERVAL", "POLL_SEC"]:
                config[key] = float(val)
            else:
                config[key] = val
        except ValueError:
            messagebox.showerror("Invalid Input", f"Invalid value for {key}: {val}")
            return

    for k, var in threshold_vars.items():
        try:
            if threshold_checks[k].get():
                config["THRESHOLDS"][k] = float(var.get())
        except ValueError:
            messagebox.showerror("Invalid Threshold", f"Channel {k}: invalid float value")
            return

    config["FULLSCREEN_MODE"] = fullscreen_var.get()
    save_config(config)

root.mainloop()