import tkinter as tk
from tkinter import messagebox
import urllib.request
import ssl
import certifi
import json
import hashlib
import tempfile
import zipfile
import os
import sys

CURRENT_VERSION = "1.0.0"

ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())

def get_latest_info():
    try:
        with urllib.request.urlopen("https://raw.githubusercontent.com/dspillmangj/SUNDAY/main/latest.json") as response:
            return json.load(response)
    except Exception as e:
        print(f"[Update Check] Failed: {e}")
        return None

def download_and_extract_update(url, expected_hash):
    try:
        with urllib.request.urlopen(url) as response:
            data = response.read()
            sha256 = hashlib.sha256(data).hexdigest()
            if sha256 != expected_hash:
                messagebox.showerror("Update Error", "Downloaded file is corrupted.\nHash mismatch.")
                return False

            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
                tmp.write(data)
                zip_path = tmp.name

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(os.getcwd())
        os.remove(zip_path)
        return True
    except Exception as e:
        messagebox.showerror("Update Failed", str(e))
        return False

def check_for_update():
    latest = get_latest_info()
    if not latest:
        return

    latest_version = latest.get("latest_version")
    download_url = latest.get("download_url")
    expected_hash = latest.get("sha256")
    notes = latest.get("notes", "")

    if not latest_version or not download_url or not expected_hash:
        return

    if latest_version == CURRENT_VERSION:
        return

    root = tk.Tk()
    root.withdraw()  # Hide main window

    msg = f"A new version ({latest_version}) is available.\n\nRelease Notes:\n{notes}\n\nWould you like to update now?"
    response = messagebox.askyesnocancel("Update Available", msg)

    if response is None:
        return  # "Remind me later" (Cancel)
    elif response is False:
        return  # "Skip" (No)
    elif response is True:
        success = download_and_extract_update(download_url, expected_hash)
        if success:
            messagebox.showinfo("Update Complete", "The application has been updated.\nPlease restart the script.")
            sys.exit(0)
# --- Perform check before anything else ---
check_for_update()

import tkinter as tk
from PIL import Image, ImageTk
import threading
import os
import socket
import struct
import time
from decimal import Decimal
from pythonosc.osc_message_builder import OscMessageBuilder
from pythonosc.osc_packet import OscPacket
from screeninfo import get_monitors
import signal
import sys
from obswebsocket import obsws, requests
import json

# --- Load Configuration ---
with open("config.json", "r") as f:
    config = json.load(f)

FULLSCREEN_MODE = config["FULLSCREEN_MODE"]
X32_IP = config["X32_IP"]
X32_PORT = config["X32_PORT"]
LOCAL_PORT = config["LOCAL_PORT"]
SUBSCRIPTION_NAME = config["SUBSCRIPTION_NAME"]
METERS_PATH = config["METERS_PATH"]
RENEW_INTERVAL = config["RENEW_INTERVAL"]
POLL_SEC = config["POLL_SEC"]
OBS_HOST = config["OBS_HOST"]
OBS_PORT = config["OBS_PORT"]
OBS_PASSWORD = config["OBS_PASSWORD"]
GROUP_CHANNELS = config["GROUP_CHANNELS"]
INDIVIDUAL_CHANNELS = config["INDIVIDUAL_CHANNELS"]
DCAS = config["DCAS"]
THRESHOLDS = {int(k): v for k, v in config["THRESHOLDS"].items()}
DISPLAY_INDEX = config["DISPLAY_INDEX"]

indicators = {}
state = {}
status = "STARTING"
lock = threading.Lock()
flashing_scribbles = {}
original_colors = {}

try:
    monitor = get_monitors()[DISPLAY_INDEX]
except IndexError:
    monitor = get_monitors()[0]
    status += "+MN"
monitor_x, monitor_y, monitor_width, monitor_height = monitor.x, monitor.y, monitor.width, monitor.height

root = tk.Tk()
root.attributes("-topmost", True)
root.overrideredirect(True)

if FULLSCREEN_MODE:
    image_width = monitor_width // 3
    image_height = monitor_height // 3
    root.geometry(f"{monitor_width}x{monitor_height}+{monitor_x}+{monitor_y}")
    status += "+FS"
else:
    image_width = monitor_width // 8
    image_height = monitor_width // 16
    root.geometry(f"{monitor_width}x{image_height}+{monitor_x}+{monitor_y}")

root.configure(bg='black')

images = []
def load_scaled_image(path, width, height):
    if not os.path.exists(path):
        print(f"Missing image: {path}")
        return None
    img = Image.open(path)
    return ImageTk.PhotoImage(img.resize((width, height), Image.LANCZOS))

for i in range(1, 9):
    suffix = " FS.png" if FULLSCREEN_MODE else ".png"
    on = load_scaled_image(f"{i}I{suffix}", image_width, image_height)
    off = load_scaled_image(f"{i}O{suffix}", image_width, image_height)
    images.append({'on': on, 'off': off})

labels = []
for i in range(8):
    lbl = tk.Label(root, bg='black')
    labels.append(lbl)

states = ['off'] * 8
if FULLSCREEN_MODE:
    positions = [
        (0, 0),                    # 1 - Top left
        (0, image_height),         # 2 - Middle left
        (0, 2 * image_height),     # 3 - Bottom left
        (image_width, 2 * image_height),  # 4 - Bottom center
        (image_width, 0),          # 5 - Top center
        (2 * image_width, 0),      # 6 - Top right
        (2 * image_width, image_height),  # 7 - Middle right
        (2 * image_width, 2 * image_height)  # 8 - Bottom right
    ]
    for i in range(8):
        labels[i].place(x=positions[i][0], y=positions[i][1], width=image_width, height=image_height)

    # --- Center Cell (Status + Logo) ---
    center_x = image_width
    center_y = image_height
    status_var = tk.StringVar(value=status.upper())
    status_label = tk.Label(
        root, textvariable=status_var, font=("Helvetica", 36, "bold"),
        fg="white", bg="black"
    )
    status_label.place(x=center_x, y=center_y + 10, width=image_width, height=50)

    logo_img_raw = Image.open("logo.png")
    max_logo_width = image_width - 40
    max_logo_height = image_height - 90  # leave space for status above
    logo_img_raw.thumbnail((max_logo_width, max_logo_height), Image.LANCZOS)
    logo_img = ImageTk.PhotoImage(logo_img_raw)
    logo_label = tk.Label(root, image=logo_img, bg='black')
    logo_label.image = logo_img
    logo_label.place(
        x=center_x + (image_width - logo_img.width()) // 2,
        y=center_y + 70,
        width=logo_img.width(),
        height=logo_img.height()
    )
else:
    for i in range(8):
        labels[i].place(x=(i * image_width), y=0, width=image_width, height=image_height)

# Function to update status label
def update_status(new_status):
    global status
    status = new_status
    if FULLSCREEN_MODE:
        status_var.set(status.upper())

# Later in osc_loop:
# Replace direct assignments to `status = ...` with:
# update_status("PROBING")
# update_status("READY")
# update_status("FALLBACK")

# NOTE: Added real-time status updates and non-stretched logo placement.

flash_tick = 0

# --- Scribble Strip Control ---
def send_scribble_color(ch, color_id):
    addr = f"/ch/{ch:02}/config/color"
    msg = OscMessageBuilder(address=addr)
    msg.add_arg(color_id, arg_type='i')
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(msg.build().dgram, (X32_IP, X32_PORT))
    sock.close()

def query_scribble_color(ch):
    addr = f"/ch/{ch:02}/config/color"
    msg = OscMessageBuilder(address=addr)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', LOCAL_PORT + ch))

    def listen():
        try:
            data, _ = sock.recvfrom(1024)
            packet = OscPacket(data)
            for m in packet.messages:
                val = int(m.message.params[0])
                with lock:
                    original_colors[ch] = val
        finally:
            sock.close()

    threading.Thread(target=listen, daemon=True).start()
    sock.sendto(msg.build().dgram, (X32_IP, X32_PORT))

# --- Cleanup Handler ---
def restore_all_scribbles():
    with lock:
        for ch, orig in original_colors.items():
            send_scribble_color(ch, orig)

def signal_handler(sig, frame):
    print("\n[Shutdown] Restoring scribble strip colors...")
    restore_all_scribbles()
    root.destroy()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

# --- Display Update ---
def update_display():
    global flash_tick
    flash_tick += 1
    flashon_state = flash_tick % 2 == 0
    flashoff_state = (flash_tick // 2) % 2 == 0

    with lock:
        for ch in range(1, 33):
            if ch not in THRESHOLDS:
                continue
            low = indicators.get(f"ch{ch}_low", False)
            indicator_key = f"mute_mic{ch}" if ch in INDIVIDUAL_CHANNELS else None
            if not indicator_key:
                for group, chans in GROUP_CHANNELS.items():
                    if ch in chans:
                        indicator_key = f"group_mute_{group}"
                        break
            if not indicator_key:
                continue

            muted = not indicators.get(indicator_key, True)

            if low and ch not in flashing_scribbles:
                query_scribble_color(ch)
                flashing_scribbles[ch] = True
            elif not low and ch in flashing_scribbles:
                orig = original_colors.get(ch)
                if orig is not None:
                    send_scribble_color(ch, orig)
                flashing_scribbles.pop(ch, None)

            if low:
                base = original_colors.get(ch, 0)
                twin = base ^ 0b1000

                state_val = indicators.get(indicator_key, 'off')
                indicator_state = 'on' if not muted else 'off'

                flash_indicator = 'flashon' if not muted else 'flashoff'

                if flash_indicator == 'flashon':
                    current = base if flashon_state else twin
                elif flash_indicator == 'flashoff':
                    current = base if flashoff_state else twin
                else:
                    current = base

                send_scribble_color(ch, current)

    for i, state in enumerate(states):
        dca_override = not indicators.get('mute_dca6', True)
        actual_state = state
        if dca_override:
            if state == 'flashon':
                actual_state = 'on'
            elif state == 'flashoff':
                actual_state = 'off'

        img = None
        if actual_state == 'on':
            img = images[i]['on']
        elif actual_state == 'off':
            img = images[i]['off']
        elif actual_state == 'flashon':
            img = images[i]['on'] if flashon_state else images[i]['off']
        elif actual_state == 'flashoff':
            img = images[i]['off'] if flashoff_state else None

        labels[i].config(image=img if img else '')
        labels[i].image = img

    root.after(500, update_display)

# --- OSC Communication ---
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

def evaluate_levels(values):
    for ch in THRESHOLDS:
        val = values[ch - 1] if ch - 1 < len(values) else 0.0
        indicators[f"ch{ch}_low"] = val <= THRESHOLDS[ch]
    for group, chans in GROUP_CHANNELS.items():
        indicators[f"group_low_{group}"] = any(indicators.get(f"ch{ch}_low", False) for ch in chans)

def resolve_state(mute_key, low_key):
    muted = not indicators.get(mute_key, True)
    low = indicators.get(low_key, False)
    if muted and low:
        return 'flashoff'
    elif not muted and low:
        return 'flashon'
    elif not muted and not low:
        return 'on'
    else:
        return 'off'

def update_booleans():
    for ch in INDIVIDUAL_CHANNELS:
        indicators[f"mute_mic{ch}"] = not state.get(ch, True)
    for group, chans in GROUP_CHANNELS.items():
        if group == 'Handheld':
            indicators[f"group_mute_{group}"] = any(not state.get(ch, True) for ch in chans)
        else:
            indicators[f"group_mute_{group}"] = all(not state.get(ch, True) for ch in chans)
    for dca in DCAS:
        indicators[f"mute_dca{dca}"] = not state.get(f"dca{dca}", True)

def update_states():
    states[0] = resolve_state('group_mute_Choir', 'group_low_Choir')
    states[1] = resolve_state('group_mute_Handheld', 'group_low_Handheld')
    states[2] = resolve_state('group_mute_Instrumental', 'group_low_Instrumental')
    states[3] = 'on' if indicators.get('mute_dca8', False) else 'off'
    states[4] = 'flashon' if not indicators.get('mute_dca7', True) else 'off'
    states[5] = resolve_state('mute_mic7', 'ch7_low')
    states[6] = resolve_state('mute_mic6', 'ch6_low')
    states[7] = resolve_state('mute_mic8', 'ch8_low')

def handle_incoming(data):
    packet = OscPacket(data)
    updated = False
    with lock:
        for raw in packet.messages:
            msg = getattr(raw, 'message', raw)
            addr = msg.address
            if "/ch/" in addr and "/mix/on" in addr:
                ch = int(addr.split("/")[2])
                muted = (msg.params[0] == 0.0)
                state[ch] = muted
                updated = True
            elif "/dca/" in addr and "/on" in addr:
                dca = int(addr.split("/")[2])
                state[f"dca{dca}"] = (msg.params[0] == 0.0)
                updated = True
    if updated:
        update_booleans()
        update_states()

def build_poll(ch):
    return OscMessageBuilder(address=f"/ch/{ch:02}/mix/on").build().dgram

def build_dca_poll(dca):
    return OscMessageBuilder(address=f"/dca/{dca}/on").build().dgram

def poll_loop(sock):
    while True:
        for ch in INDIVIDUAL_CHANNELS + sum(GROUP_CHANNELS.values(), []):
            sock.sendto(build_poll(ch), (X32_IP, X32_PORT))
        for dca in DCAS:
            sock.sendto(build_dca_poll(dca), (X32_IP, X32_PORT))
        time.sleep(POLL_SEC)

def receive_loop(sock):
    try:
        while True:
            data, _ = sock.recvfrom(4096)
            if len(data) > 225:
                values = parse_x32_meter_blob(data)
                evaluate_levels(values)
                update_states()
            else:
                handle_incoming(data)
    except OSError:
        print("[receive_loop] Socket closed. Exiting thread.")

def phantom_power(sock, state):
    value = 1 if state == 'on' else 0
    address = "/headamp/037/phantom"
    send_osc_message(sock, address, 'i', [value])
    print(f"[Phantom] Set to {state.upper()} on /headamp/037/phantom")

def verify_flash():
    for attempt in range(3):
        time.sleep(5.5)
        if states[5] in ['flashon', 'flashoff']:
            print(f"[Startup Check] Flash state detected: {states[5]}")
            return True
        print(f"[Startup Check] Attempt {attempt + 1}: Verifying flash trigger...")
    print("[Startup Check] Verification failed.")
    return False

def start_subscription(sock):
    send_osc_message(sock, '/batchsubscribe', 'ssiii', [SUBSCRIPTION_NAME, METERS_PATH, 0, 0, 0])
    def renew():
        while True:
            time.sleep(RENEW_INTERVAL)
            send_osc_message(sock, '/renew', 's', [SUBSCRIPTION_NAME])
            print("[OSC] Sent /renew")
    threading.Thread(target=renew, daemon=True).start()

# --- OBS Streaming Status Check ---
def check_obs_streaming():
    ws = obsws(OBS_HOST, OBS_PORT, OBS_PASSWORD)
    try:
        ws.connect()
        response = ws.call(requests.GetStreamStatus())
        streaming = response.datain.get("outputActive", False)
        return streaming
    except Exception as e:
        print(f"[ERROR] Could not connect to OBS: {e}")
        return False
    finally:
        ws.disconnect()

def obs_control_dca8_loop():
    while True:
        streaming = check_obs_streaming()
        print(f"[OBS Monitor] Streaming status: {streaming}")
        with lock:
            desired_mute = not streaming
            current_mute = state.get('dca8', True)
            print(f"[OBS Monitor] Desired mute: {desired_mute}, Current mute: {current_mute}")
            if current_mute != desired_mute:
                print(f"[OBS Monitor] Sending OSC to {'unmute' if streaming else 'mute'} DCA8")
                send_osc_message(osc_sock, "/dca/8/on", 'i', [1 if streaming else 0])

                # Force internal and visual update
                state['dca8'] = desired_mute
                indicators['mute_dca8'] = not desired_mute
                update_booleans()
                update_states()

                # Force a poll to X32 to make sure mute sticks
                osc_sock.sendto(build_dca_poll(8), (X32_IP, X32_PORT))
        time.sleep(1)

# --- Main OSC loop and program startup ---
def osc_loop():
    global osc_sock, status
    while True:
        osc_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        osc_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            osc_sock.bind(('', LOCAL_PORT))
        except OSError:
            print("[OSC] Port in use. Retrying...")
            time.sleep(1)
            continue

        threading.Thread(target=receive_loop, args=(osc_sock,), daemon=True).start()
        threading.Thread(target=poll_loop, args=(osc_sock,), daemon=True).start()
        start_subscription(osc_sock)

        phantom_power(osc_sock, 'off')
        update_status("PROBING")
        if verify_flash():
            phantom_power(osc_sock, 'on')
            update_status("READY")
            break
        else:
            phantom_power(osc_sock, 'on')
            osc_sock.close()
            print("[OSC] Restarting OSC communication...")
            time.sleep(2)
    start_subscription(osc_sock)

# Start OSC loop thread
threading.Thread(target=osc_loop, daemon=True).start()

def start_obs_thread_when_ready():
    while 'osc_sock' not in globals():
        time.sleep(0.1)
    time.sleep(1)  # Give extra moment to fully initialize
    threading.Thread(target=obs_control_dca8_loop, daemon=True).start()

threading.Thread(target=start_obs_thread_when_ready, daemon=True).start()

root.after(0, update_display)
root.mainloop()