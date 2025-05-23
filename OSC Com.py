import tkinter as tk
from decimal import Decimal, ROUND_HALF_UP
import struct
import socket
import threading
import time
from pythonosc.osc_message_builder import OscMessageBuilder
from pythonosc.osc_packet import OscPacket

# ——— CONFIGURATION ———
X32_IP = '192.168.3.110'
X32_PORT = 10023
LOCAL_PORT = 10024
SUBSCRIPTION_NAME = 'mtrs'
METERS_PATH = '/meters/1'
RENEW_INTERVAL = 9
POLL_SEC = 0.05

# GUI state: groups and channels to track
GROUPS = {
    'Instrumental': {'channels': [3, 4, 5], 'threshold': 0.0002},
    'Handheld':     {'channels': [9, 10, 11, 12], 'threshold': 0.0000},
    'Choir':        {'channels': [13, 14, 15, 16], 'threshold': 0.0001}
}

INDIVIDUALS = {
    'Lapel':   {'channel': 6, 'threshold': 0.0000},
    'Pulpit':  {'channel': 7, 'threshold': 0.0000},
    'White':   {'channel': 8, 'threshold': 0.0001}
}

INDIVIDUAL_CHANNELS = [6, 7, 8]
GROUP_CHANNELS = {
    'Instrumental': [3, 4, 5],
    'Handheld': [9, 10, 11, 12],
    'Choir': [13, 14, 15, 16]
}
DCAS = [6, 7, 8]

low_status = {}
channel_status = {}
channel_values = {}
mute_booleans = {
    'mic6': False,
    'mic7': False,
    'mic8': False,
    'Instrumental': False,
    'Handheld': False,
    'Choir': False,
    'dca6': False,
    'dca7': False,
    'dca8': False
}
state = {}
lock = threading.Lock()

root = tk.Tk()
root.title("S.U.N.D.A.Y.")
root.geometry("1200x600")
root.configure(bg='black')

left_frame = tk.Frame(root, bg='black')
left_frame.pack(side='left', expand=True, fill='both', padx=10, pady=10)

right_frame = tk.Frame(root, bg='black')
right_frame.pack(side='right', expand=True, fill='both', padx=10, pady=10)

labels = {}
box_id = 1

def make_box(name, parent):
    global box_id
    lbl = tk.Label(parent, text=f"I{box_id}: {name}", width=16, height=3,
                   font=('Arial', 12), bg='gray', fg='white', relief='raised')
    lbl.pack(side='left', padx=4, pady=4)
    labels[name] = lbl
    box_id += 1
    return lbl

for group_name, info in GROUPS.items():
    group_frame = tk.Frame(left_frame, bg='black')
    group_frame.pack(anchor='w')
    make_box(group_name, group_frame)
    for ch in info['channels']:
        make_box(f"ch{ch}", group_frame)

indiv_frame = tk.Frame(left_frame, bg='black')
indiv_frame.pack(anchor='w')
for name, info in INDIVIDUALS.items():
    make_box(name, indiv_frame)
    make_box(f"ch{info['channel']}", indiv_frame)

mute_frame = tk.Frame(right_frame, bg='black')
mute_frame.pack(fill='both', expand=True)

for name in mute_booleans:
    make_box(f"mute_{name}", mute_frame)

def send_osc_message(sock, address, types, args):
    address_bytes = address.encode('utf-8')
    address_padded = address_bytes + b'\x00' * (4 - (len(address_bytes) % 4))
    type_tags = ',' + types
    type_tags_bytes = type_tags.encode('utf-8')
    type_tags_padded = type_tags_bytes + b'\x00' * (4 - (len(type_tags_bytes) % 4))
    args_bytes = b''
    for tag, arg in zip(types, args):
        if tag == 'i':
            args_bytes += struct.pack('>i', arg)
        elif tag == 'f':
            args_bytes += struct.pack('>f', arg)
        elif tag == 's':
            arg_bytes = arg.encode('utf-8')
            arg_padded = arg_bytes + b'\x00' * (4 - (len(arg_bytes) % 4))
            args_bytes += arg_padded
    message = address_padded + type_tags_padded + args_bytes
    sock.sendto(message, (X32_IP, X32_PORT))

def parse_x32_meter_blob(data):
    header_length = 12
    blob = data[header_length:]
    blob_size = struct.unpack('>I', blob[:4])[0]
    num_values = struct.unpack('<I', blob[4:8])[0]
    float_data = blob[8:]
    if len(float_data) < num_values * 4:
        raise ValueError("Incomplete float data in blob.")
    values = struct.unpack('<' + 'f' * num_values, float_data[:num_values * 4])
    return [
        float(Decimal(str(val)).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP))
        for val in values
    ]

def evaluate_levels(values):
    for ch in range(1, len(values) + 1):
        channel_values[ch] = values[ch - 1]
    for ch, val in channel_values.items():
        channel_status[ch] = val
    for group, info in GROUPS.items():
        thresh = info['threshold']
        chs = info['channels']
        low_status[group] = any(channel_values.get(ch, 0.0) <= thresh for ch in chs)
        for ch in chs:
            low_status[f"ch{ch}"] = channel_values.get(ch, 0.0) <= thresh
    for name, info in INDIVIDUALS.items():
        ch = info['channel']
        thresh = info['threshold']
        low_status[name] = channel_values.get(ch, 0.0) <= thresh
        low_status[f"ch{ch}"] = channel_values.get(ch, 0.0) <= thresh

def update_gui():
    for name, lbl in labels.items():
        if name.startswith("mute_"):
            key = name.replace("mute_", "")
            val = mute_booleans.get(key, False)
            lbl.config(bg='green' if val else 'red')
        else:
            val = low_status.get(name, False)
            lbl.config(bg='red' if val else 'green')
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

def osc_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', LOCAL_PORT))
    threading.Thread(target=receive_loop, args=(sock,), daemon=True).start()
    send_osc_message(sock, '/batchsubscribe', 'ssiii', [SUBSCRIPTION_NAME, METERS_PATH, 0, 0, 0])
    print("Subscribed to /meters/1")
    threading.Thread(target=poll_loop, args=(sock,), daemon=True).start()
    try:
        while True:
            time.sleep(RENEW_INTERVAL)
            send_osc_message(sock, '/renew', 's', [SUBSCRIPTION_NAME])
    except KeyboardInterrupt:
        print("Exiting OSC")

def unwrap_msg(msg):
    return getattr(msg, 'message', msg)

def update_booleans():
    mute_booleans['mic6'] = not state.get(6, True)
    mute_booleans['mic7'] = not state.get(7, True)
    mute_booleans['mic8'] = not state.get(8, True)
    for group, chans in GROUP_CHANNELS.items():
        if group == 'Handheld':
            mute_booleans[group] = any(not state.get(ch, True) for ch in chans)
        else:
            mute_booleans[group] = all(not state.get(ch, True) for ch in chans)
    mute_booleans['dca6'] = not state.get("dca6", True)
    mute_booleans['dca7'] = not state.get("dca7", True)
    mute_booleans['dca8'] = not state.get("dca8", True)

def handle_incoming(data):
    packet = OscPacket(data)
    updated = False
    with lock:
        for raw in packet.messages:
            msg = unwrap_msg(raw)
            addr = msg.address
            if "/ch/" in addr and "/mix/on" in addr:
                ch = int(addr.split("/")[2])
                muted = (msg.params[0] == 0.0)
                prev = state.get(ch)
                state[ch] = muted
                if prev is None or prev != muted:
                    updated = True
            elif "/dca/" in addr and "/on" in addr:
                dca = int(addr.split("/")[2])
                key = f"dca{dca}"
                muted = (msg.params[0] == 0.0)
                prev = state.get(key)
                state[key] = muted
                if prev is None or prev != muted:
                    updated = True
        if updated:
            update_booleans()

def build_poll(ch):
    return OscMessageBuilder(address=f"/ch/{ch:02}/mix/on").build().dgram

def build_dca_poll(dca_index):
    return OscMessageBuilder(address=f"/dca/{dca_index}/on").build().dgram

def poll_loop(sock):
    while True:
        for ch in INDIVIDUAL_CHANNELS + sum(GROUP_CHANNELS.values(), []):
            sock.sendto(build_poll(ch), (X32_IP, X32_PORT))
        for dca in DCAS:
            sock.sendto(build_dca_poll(dca), (X32_IP, X32_PORT))
        time.sleep(POLL_SEC)

threading.Thread(target=osc_loop, daemon=True).start()
root.after(100, update_gui)
root.mainloop()
