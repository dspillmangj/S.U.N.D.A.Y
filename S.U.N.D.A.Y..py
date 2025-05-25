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

# --- Configuration ---
X32_IP = '192.168.3.110'
X32_PORT = 10023
LOCAL_PORT = 10024
SUBSCRIPTION_NAME = 'mtrs'
METERS_PATH = '/meters/1'
RENEW_INTERVAL = 9
POLL_SEC = 0.05

GROUP_CHANNELS = {
    'Instrumental': [3, 4, 5],
    'Handheld': [9, 10, 11, 12],
    'Choir': [13, 14, 15, 16]
}
INDIVIDUAL_CHANNELS = [6, 7, 8]
DCAS = [6, 7, 8]

THRESHOLDS = {
    3: 0.0000165134,  # Instrumental
    4: 0.0000148019,  # Instrumental
    5: 0.0000151569,  # Instrumental
    6: 0.0000173300,  # Lapel
    7: 0.0000421634,  # Pulpit
    8: 0.0000298422,  # White
    9: 0.0000536250,  # Handheld
    10: 0.0000735157,  # Handheld
    11: 0.0000388628,  # Handheld
    12: 0.0000366700,  # Handheld
    13: 0.0000306327,  # Choir
    14: 0.0000243578,  # Choir
    15: 0.0000200554,  # Choir
    16: 0.0000227090  # Choir
}

indicators = {}
state = {}
lock = threading.Lock()

# --- GUI Initialization ---
from screeninfo import get_monitors
DISPLAY_INDEX = 0
monitor = get_monitors()[DISPLAY_INDEX]
monitor_x, monitor_y, monitor_width = monitor.x, monitor.y, monitor.width

root = tk.Tk()
root.attributes("-topmost", True)
root.overrideredirect(True)
image_width, image_height = monitor_width // 8, monitor_width // 16
root.geometry(f"{monitor_width}x{image_height}+{monitor_x}+{monitor_y}")
root.configure(bg='black')

# Load images
images = []
def load_scaled_image(path, width, height):
    if not os.path.exists(path):
        print(f"Missing image: {path}")
        return None
    img = Image.open(path)
    img = img.resize((width, height), Image.LANCZOS)
    return ImageTk.PhotoImage(img)

for i in range(1, 9):
    on = load_scaled_image(f"{i}I.png", image_width, image_height)
    off = load_scaled_image(f"{i}O.png", image_width, image_height)
    images.append({'on': on, 'off': off})

labels = []
states = ['off'] * 8
for i in range(8):
    lbl = tk.Label(root, bg='black')
    lbl.place(x=i * image_width, y=0, width=image_width, height=image_height)
    labels.append(lbl)

# Flashing logic
flash_tick = 0

def update_display():
    global flash_tick
    flash_tick += 1
    flashon_state = flash_tick % 2 == 0
    flashoff_state = (flash_tick // 2) % 2 == 0

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

def update_states():
    states[0] = resolve_state('group_mute_Choir', 'group_low_Choir')
    states[1] = resolve_state('group_mute_Handheld', 'group_low_Handheld')
    states[2] = resolve_state('group_mute_Instrumental', 'group_low_Instrumental')
    states[3] = 'on' if indicators.get('mute_dca8', False) else 'off'
    states[4] = 'flashon' if not indicators.get('mute_dca7', True) else 'off'
    states[5] = resolve_state('mute_mic7', 'ch7_low')
    states[6] = resolve_state('mute_mic6', 'ch6_low')
    states[7] = resolve_state('mute_mic8', 'ch8_low')

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

def receive_loop(sock):
    while True:
        try:
            data, _ = sock.recvfrom(4096)
            if len(data) > 225:
                values = parse_x32_meter_blob(data)
                evaluate_levels(values)
            else:
                handle_incoming(data)
        except Exception as e:
            print("Error:", e)

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

def osc_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', LOCAL_PORT))
    threading.Thread(target=receive_loop, args=(sock,), daemon=True).start()
    send_osc_message(sock, '/batchsubscribe', 'ssiii', [SUBSCRIPTION_NAME, METERS_PATH, 0, 0, 0])
    threading.Thread(target=poll_loop, args=(sock,), daemon=True).start()
    try:
        while True:
            time.sleep(RENEW_INTERVAL)
            send_osc_message(sock, '/renew', 's', [SUBSCRIPTION_NAME])
    except KeyboardInterrupt:
        print("Exiting OSC")

# --- Start everything ---
threading.Thread(target=osc_loop, daemon=True).start()
root.after(0, update_display)
root.mainloop()
