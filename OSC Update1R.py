import tkinter as tk
from decimal import Decimal
import struct
import socket
import threading
import time
from pythonosc.osc_message_builder import OscMessageBuilder
from pythonosc.osc_packet import OscPacket

# ---------------- CONFIGURATION ----------------
X32_IP = '192.168.3.110'
X32_PORT = 10023
LOCAL_PORT = 10024
SUBSCRIPTION_NAME = 'mtrs'
METERS_PATH = '/meters/1'
RENEW_INTERVAL = 9
POLL_SEC = 0.05

# Channel thresholds
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

GROUP_CHANNELS = {
    'Instrumental': [3, 4, 5],
    'Handheld': [9, 10, 11, 12],
    'Choir': [13, 14, 15, 16]
}
INDIVIDUAL_CHANNELS = [6, 7, 8]
DCAS = [6, 7, 8]

indicators = {}  # Unified indicators dict
channel_values = {}
channel_min = {}
channel_max = {}
state = {}
lock = threading.Lock()

root = tk.Tk()
root.title("S.U.N.D.A.Y.")
root.geometry("1400x900")
root.configure(bg='black')

main_frame = tk.Frame(root, bg='black')
main_frame.pack(fill='both', expand=True)

canvas = tk.Canvas(main_frame, bg='black')
scrollbar = tk.Scrollbar(main_frame, orient='vertical', command=canvas.yview)
scrollable_frame = tk.Frame(canvas, bg='black')
scrollable_frame.bind(
    "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
)
canvas.create_window((0, 0), window=scrollable_frame, anchor='nw')
canvas.configure(yscrollcommand=scrollbar.set)
canvas.pack(side="left", fill="both", expand=True)
scrollbar.pack(side="right", fill="y")

labels = {}


def make_box(name, parent):
    frame = tk.Frame(parent, bg='black')
    label = tk.Label(frame, text=name, width=12, font=('Arial', 10), bg='gray', fg='white')
    label.pack(side='left', padx=2)
    val = tk.Label(frame, text='0.0000000000', font=('Courier', 10), width=15, bg='black', fg='white')
    val.pack(side='left')
    min_val = tk.Label(frame, text='min: 0.0000000000', font=('Courier', 10), width=22, bg='black', fg='white')
    min_val.pack(side='left')
    max_val = tk.Label(frame, text='max: 0.0000000000', font=('Courier', 10), width=22, bg='black', fg='white')
    max_val.pack(side='left')
    frame.pack(anchor='w', pady=2)
    labels[name] = (label, val, min_val, max_val)


for ch in sorted(THRESHOLDS.keys()):
    make_box(f"ch{ch}", scrollable_frame)
for name in GROUP_CHANNELS.keys():
    make_box(f"group_low_{name}", scrollable_frame)
    make_box(f"group_mute_{name}", scrollable_frame)
for ch in INDIVIDUAL_CHANNELS:
    make_box(f"mute_mic{ch}", scrollable_frame)
for dca in DCAS:
    make_box(f"mute_dca{dca}", scrollable_frame)


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
        channel_values[ch] = val
        indicators[f"ch{ch}_low"] = val <= THRESHOLDS[ch]
        indicators[f"ch{ch}_val"] = val
        if ch not in channel_min or val < channel_min[ch]:
            channel_min[ch] = val
        if ch not in channel_max or val > channel_max[ch]:
            channel_max[ch] = val
    for group, chans in GROUP_CHANNELS.items():
        group_key = f"group_low_{group}"
        indicators[group_key] = any(indicators.get(f"ch{ch}_low", False) for ch in chans)
        if group_key not in indicators:
            indicators[group_key] = False  # Fallback in case evaluation fails


def update_gui():
    for key, (lbl, val_lbl, min_lbl, max_lbl) in labels.items():
        if key.startswith("ch"):
            ch = int(key.replace("ch", ""))
            val = indicators.get(f"ch{ch}_val", 0.0)
            low = indicators.get(f"ch{ch}_low", False)
            val_lbl.config(text=f"{val:.10f}", bg='red' if low else 'green')
            min_lbl.config(text=f"min: {channel_min.get(ch, 0.0):.10f}")
            max_lbl.config(text=f"max: {channel_max.get(ch, 0.0):.10f}")
        elif key.startswith("group_low_"):
            status = indicators.get(key, False)
            val_lbl.config(text=str(status), bg='red' if status else 'green')
        elif key.startswith("group_mute_") or key.startswith("mute_mic") or key.startswith("mute_dca"):
            status = indicators.get(key, False)
            val_lbl.config(text=str(status), bg='green' if status else 'red')
    root.after(250, update_gui)


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
                prev = state.get(ch)
                state[ch] = muted
                if prev is None or prev != (not muted):
                    updated = True
            elif "/dca/" in addr and "/on" in addr:
                dca = int(addr.split("/")[2])
                key = f"dca{dca}"
                muted = (msg.params[0] == 0.0)
                prev = state.get(key)
                state[key] = muted
                if prev is None or prev != (not muted):
                    updated = True
    if updated:
        update_booleans()


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
    print(indicators)


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

threading.Thread(target=osc_loop, daemon=True).start()
root.after(100, update_gui)
root.mainloop()
