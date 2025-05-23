import tkinter as tk
from decimal import Decimal, ROUND_HALF_UP
import struct
import socket
import threading
import time
import os
from pythonosc.osc_message_builder import OscMessageBuilder
from pythonosc.osc_packet import OscPacket
from PIL import Image, ImageTk

# ——— CONFIGURATION (from first script) ———
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

low_status = {} # This will contain the 'I' inputs related to channel levels (e.g., 'ch6', 'Instrumental' being low)
channel_status = {} # Direct channel values (not used for 'I' inputs directly, but for low_status)
channel_values = {} # Raw channel meter values
mute_booleans = { # This will contain the 'I' inputs related to mute states (e.g., 'mute_mic6', 'mute_dca6')
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
state = {} # Internal state for X32 mute polling
lock = threading.Lock()


# ——— CONFIGURATION (from second script, adapted) ———
DISPLAY_INDEX = 0  # Change this to choose which display (0 = primary, 1 = secondary, etc.)

try:
    import screeninfo
except ImportError:
    print("Please install screeninfo: pip install screeninfo")
    exit(1)

# Get monitor info
monitors = screeninfo.get_monitors()
if DISPLAY_INDEX >= len(monitors):
    print(f"Invalid DISPLAY_INDEX: {DISPLAY_INDEX}, only {len(monitors)} monitor(s) detected.")
    exit(1)

monitor = monitors[DISPLAY_INDEX]
monitor_x = monitor.x
monitor_y = monitor.y
monitor_width = monitor.width


# ——— GUI Setup (from second script, adapted) ———
root = tk.Tk()
root.attributes("-topmost", True)
root.overrideredirect(True)

# Calculate layout based on selected monitor
image_width = monitor_width // 8
image_height = monitor_width // 16 # Keep this proportional to width for large displays
root.geometry(f"{monitor_width}x{image_height}+{monitor_x}+{monitor_y}")
root.configure(bg='black')

# Load and resize image
def load_scaled_image(path, width, height):
    if not os.path.exists(path):
        print(f"Missing image: {path} - Please ensure all 'I' and 'O' image files are in the script directory.")
        # Create a placeholder if image is missing
        placeholder = Image.new('RGB', (width, height), color = 'red')
        d = ImageDraw.Draw(placeholder)
        d.text((width/4, height/4), "MISSING", fill=(255,255,255))
        return ImageTk.PhotoImage(placeholder)
    image = Image.open(path)
    image = image.resize((width, height), Image.LANCZOS)
    return ImageTk.PhotoImage(image)

# Load all indicator images
images = []
# Ensure all 8 indicators have 'on' and 'off' images
for i in range(1, 9):
    on_img = load_scaled_image(f"{i}I.png", image_width, image_height)
    off_img = load_scaled_image(f"{i}O.png", image_width, image_height)
    images.append({'on': on_img, 'off': off_img})

# GUI Labels for the 8 indicators and their default states
labels_L = [] # Renamed to avoid clash with original 'labels'
states_L = ["off"] * 8  # Start all L indicators in 'off' state
for i in range(8):
    lbl = tk.Label(root, bg='black')
    lbl.place(x=i * image_width, y=0, width=image_width, height=image_height)
    labels_L.append(lbl)

# Flashing logic (synchronized)
flash_tick = 0  # Advances every 0.5 seconds

def update_L_display():
    global flash_tick
    flash_tick += 1
    flashon_state = flash_tick % 2 == 0              # Toggles every 0.5s
    flashoff_state = (flash_tick // 2) % 2 == 0       # Toggles every 1s

    for i in range(8):
        state = states_L[i]
        img = None

        if state == "on":
            img = images[i]['on']
        elif state == "off":
            img = images[i]['off']
        elif state == "flashon":
            img = images[i]['on'] if flashon_state else images[i]['off']
        elif state == "flashoff":
            img = images[i]['off'] if flashoff_state else images[i]['off'] # Changed from None to off for FLASHOFF
        elif state == "blank":
            img = None # Or use a specific blank image if you have one

        if img:
            labels_L[i].config(image=img)
            labels_L[i].image = img # Keep a reference!
        else:
            labels_L[i].config(image='', bg='black') # Clear image, ensure background is black
            labels_L[i].image = None

    root.after(500, update_L_display)  # Update every 0.5s (syncs all flashing)


# ——— Bridge Logic: Map X32 states to L indicators ———

# This dictionary maps your 'I' identifiers to the actual status values.
# It will be populated dynamically based on mute_booleans and low_status.
# True means ON, False means OFF
input_states = {}

def get_input_state(key):
    """Helper to retrieve the boolean state for an 'I' input."""
    # Mute booleans are inverted, so True means muted (OFF for this logic)
    # low_status means below threshold (OFF for this logic)
    if key.startswith('mute_'):
        return mute_booleans.get(key.replace('mute_', ''), False) # False if muted means 'ON' for our logic
    elif key.startswith('ch'):
        # For chX, low_status[f"ch{ch}"] is True if level is LOW, which means OFF for this logic
        return low_status.get(key, False) # False if low means 'ON' for our logic
    elif key in ['Instrumental', 'Handheld', 'Choir', 'Lapel', 'Pulpit', 'White']:
        return low_status.get(key, False)
    elif key.startswith('dca'):
        return mute_booleans.get(key, False)
    else:
        # Default to OFF if the key isn't recognized, or for non-boolean status
        return False # This will need adjustment if any 'I' is not directly from mute_booleans or low_status

# The 'I' inputs we care about for the L indicators
# We use simple string keys, and their boolean value will be derived from `low_status` and `mute_booleans`
# based on their meaning (e.g., 'I26' might map to `not mute_booleans['dca6']` if it implies DCA 6 is ON)

# Mapping from 'I' identifiers in your rules to the actual state variables
# This is a crucial step. Based on the previous script,
# I'm making educated guesses about what 'I' refers to.
# This assumes 'I#' refers to the state of an indicator in the first script's primitive GUI,
# and these are driven by `low_status` or `mute_booleans`.
# It seems 'I' with a number corresponds to the box_id of the original GUI.
# This part is highly dependent on how your original first script assigned box_id to its labels.
# If 'I26' really means 'dca6' mute status, then `input_states['I26']` should reflect that.

# Let's try to infer from the context:
# I26, I25, I24 -> likely related to mute_booleans for DCAs or similar
# I10, I5, I1 -> likely related to low_status of specific channels or groups
# I27 -> A global control, perhaps related to the 'Pulpit' mic or similar
# I29, I28 -> Other controls

# We need a clear mapping from 'I##' in the rules to specific variables in `low_status` or `mute_booleans`.
# Since the first script's `make_box` assigns `box_id` incrementally, it's hard to directly map `I26` etc.
# to a specific channel/DCA/group.

# Let's assume for now that the 'I' numbers in the rules refer to logical states
# derived from the `low_status` and `mute_booleans` based on their *names* in the original script.
# This is the most critical part to get right based on your specific setup.
# I'll create a dummy `input_states` that you *must* adjust if my assumptions are wrong.

# === IMPORTANT: YOU MUST ADJUST THIS MAPPING ===
# This is a placeholder for how your 'I' inputs map to actual script variables.
# The 'I' numbers in your rules (I26, I10, etc.) are NOT directly related to the `box_id` from the first script.
# They are logical inputs. You need to tell me what each 'I##' refers to in terms of
# `low_status` or `mute_booleans`.

# EXAMPLE MAPPING (YOU NEED TO VERIFY/CORRECT THIS BASED ON YOUR INTENT)
# For instance, if:
# I26 means 'Pulpit' channel is NOT low (level is good) -> not low_status['Pulpit']
# I10 means 'Handheld' group is muted -> mute_booleans['Handheld']
# I27 means 'Pulpit' channel is muted -> mute_booleans['mic7']
# I29 means 'dca6' is muted -> mute_booleans['dca6']
# I28 means 'dca7' is muted -> mute_booleans['dca7']
# And for others, they might be:
# I25: not low_status['Instrumental']
# I5: mute_booleans['Instrumental']
# I24: not low_status['Choir']
# I1: mute_booleans['Choir']
# I22: not low_status['Lapel']
# I18: mute_booleans['mic6']
# I21: not low_status['White']
# I16: mute_booleans['mic8']
# I23: not low_status['dca8'] (assuming this is a state where DCA 8 is active/on, i.e., not muted)
# I20: mute_booleans['dca8']


# Let's try to create a *plausible* mapping based on the available variables.
# This still requires your verification!
# The `get_input_state` function is the place to define how each `I` maps to `low_status` or `mute_booleans`.

def get_i_state(i_identifier):
    """
    Translates an 'I' identifier from the rules into a boolean state
    (True for ON, False for OFF) based on `low_status` and `mute_booleans`.
    THIS IS THE MOST CRITICAL PART TO VERIFY/ADJUST.
    """
    with lock: # Ensure consistent state reading
        if i_identifier == 'I26': return not low_status.get('Pulpit', True) # Pulpit is ON if NOT low
        if i_identifier == 'I10': return mute_booleans.get('Handheld', False) # Handheld is ON if MUTED
        if i_identifier == 'I27': return mute_booleans.get('mic7', False) # mic7 is ON if MUTED
        if i_identifier == 'I25': return not low_status.get('Instrumental', True) # Instrumental is ON if NOT low
        if i_identifier == 'I5':  return mute_booleans.get('Instrumental', False) # Instrumental is ON if MUTED
        if i_identifier == 'I24': return not low_status.get('Choir', True) # Choir is ON if NOT low
        if i_identifier == 'I1':  return mute_booleans.get('Choir', False) # Choir is ON if MUTED
        if i_identifier == 'I29': return mute_booleans.get('dca6', False) # dca6 is ON if MUTED
        if i_identifier == 'I28': return mute_booleans.get('dca7', False) # dca7 is ON if MUTED
        if i_identifier == 'I22': return not low_status.get('Lapel', True) # Lapel is ON if NOT low
        if i_identifier == 'I18': return mute_booleans.get('mic6', False) # mic6 is ON if MUTED
        if i_identifier == 'I21': return not low_status.get('White', True) # White is ON if NOT low
        if i_identifier == 'I16': return mute_booleans.get('mic8', False) # mic8 is ON if MUTED
        if i_identifier == 'I23': return not low_status.get('Pulpit', True) # Assuming I23 for 'Pulpit' as another indicator logic
        if i_identifier == 'I20': return mute_booleans.get('dca8', False) # dca8 is ON if MUTED

    print(f"Warning: I-identifier '{i_identifier}' not mapped. Defaulting to False.")
    return False

def evaluate_indicator_states():
    """Applies the rules from the third document to update states_L."""
    global states_L

    # L1 Logic
    i26_on = get_i_state('I26')
    i10_on = get_i_state('I10')
    i27_on = get_i_state('I27')

    if i10_on:
        states_L[0] = "ON" if i26_on else "OFF"
    else: # I10 OFF
        if i27_on: # I27 ON
            states_L[0] = "ON" if i26_on else "OFF"
        else: # I27 OFF
            states_L[0] = "FLASHON" if i26_on else "FLASHOFF"

    # L2 Logic
    i25_on = get_i_state('I25')
    i5_on = get_i_state('I5')
    # i27_on is already evaluated

    if i5_on:
        states_L[1] = "ON" if i25_on else "OFF"
    else: # I5 OFF
        if i27_on: # I27 ON
            states_L[1] = "ON" if i25_on else "OFF"
        else: # I27 OFF
            states_L[1] = "FLASHON" if i25_on else "FLASHOFF"

    # L3 Logic
    i24_on = get_i_state('I24')
    i1_on = get_i_state('I1')
    # i27_on is already evaluated

    if i1_on:
        states_L[2] = "ON" if i24_on else "OFF"
    else: # I1 OFF
        if i27_on: # I27 ON
            states_L[2] = "ON" if i24_on else "OFF"
        else: # I27 OFF
            states_L[2] = "FLASHON" if i24_on else "FLASHOFF"

    # L4 Logic
    i29_on = get_i_state('I29')
    states_L[3] = "OFF" if i29_on else "ON"

    # L5 Logic
    i28_on = get_i_state('I28')
    states_L[4] = "OFF" if i28_on else "FLASHON"

    # L6 Logic
    i22_on = get_i_state('I22')
    i18_on = get_i_state('I18')
    # i27_on is already evaluated

    if i18_on:
        states_L[5] = "ON" if i22_on else "OFF"
    else: # I18 OFF
        if i27_on: # I27 ON
            states_L[5] = "ON" if i22_on else "OFF"
        else: # I27 OFF
            states_L[5] = "FLASHON" if i22_on else "FLASHOFF"

    # L7 Logic
    i21_on = get_i_state('I21')
    i16_on = get_i_state('I16')
    # i27_on is already evaluated

    if i16_on:
        states_L[6] = "ON" if i21_on else "OFF"
    else: # I16 OFF
        if i27_on: # I27 ON
            states_L[6] = "ON" if i21_on else "OFF"
        else: # I27 OFF
            states_L[6] = "FLASHON" if i21_on else "FLASHOFF"

    # L8 Logic
    i23_on = get_i_state('I23')
    i20_on = get_i_state('I20')
    # i27_on is already evaluated

    if i20_on:
    # Changed from I23 to I20 for logic, as I20 is the *second* condition in the rule.
    # The rule is "I23 OFF and I20 ON: L8 OFF", "I23 ON and I20 ON: L8 ON"
        states_L[7] = "ON" if i23_on else "OFF"
    else: # I20 OFF
        if i27_on: # I27 ON
            states_L[7] = "ON" if i23_on else "OFF"
        else: # I27 OFF
            states_L[7] = "FLASHON" if i23_on else "FLASHOFF"


# ——— Original X32 Communication Functions (unchanged) ———
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
    """
    Evaluates levels and updates low_status for channels and groups.
    This generates the 'I' inputs related to audio levels.
    """
    with lock:
        for ch in range(1, len(values) + 1):
            channel_values[ch] = values[ch - 1]
        for ch, val in channel_values.items():
            channel_status[ch] = val # channel_status is just a copy of channel_values here
        for group, info in GROUPS.items():
            thresh = info['threshold']
            chs = info['channels']
            # A group is 'low_status' (TRUE) if ANY of its channels are at or below threshold
            low_status[group] = any(channel_values.get(ch, 0.0) <= thresh for ch in chs)
            # Individual channel status within groups
            for ch in chs:
                low_status[f"ch{ch}"] = channel_values.get(ch, 0.0) <= thresh
        for name, info in INDIVIDUALS.items():
            ch = info['channel']
            thresh = info['threshold']
            # Individual mic is 'low_status' (TRUE) if its level is at or below threshold
            low_status[name] = channel_values.get(ch, 0.0) <= thresh
            low_status[f"ch{ch}"] = channel_values.get(ch, 0.0) <= thresh

def receive_loop(sock):
    while True:
        try:
            data, _ = sock.recvfrom(4096)
            if len(data) > 225: # Heuristic to differentiate meter blob from other OSC messages
                values = parse_x32_meter_blob(data)
                evaluate_levels(values)
                # After updating low_status, re-evaluate L indicators
                evaluate_indicator_states()
            else:
                handle_incoming(data)
                # After updating mute_booleans, re-evaluate L indicators
                evaluate_indicator_states()
        except Exception as e:
            print(f"Error in receive_loop: {e}")

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
        send_osc_message(sock, '/unsubscribe', 's', [SUBSCRIPTION_NAME]) # Clean up subscription
    finally:
        sock.close()


def unwrap_msg(msg):
    # This function is used to handle potential python-osc bundle parsing variations.
    # It attempts to get the 'message' attribute if the object is a bundle, otherwise returns the object itself.
    return getattr(msg, 'message', msg)

def update_booleans():
    """
    Updates mute_booleans based on the 'state' dictionary.
    This generates the 'I' inputs related to mute states.
    """
    with lock:
        # Note: In X32, 0.0 usually means ON (unmuted), 1.0 means OFF (muted).
        # Your original script's `muted = (msg.params[0] == 0.0)` implies 0.0 is Muted.
        # Let's assume your original script's `mute_booleans` means TRUE if Muted.
        # So, if state[ch] is True, it means the channel IS muted.
        # If msg.params[0] == 0.0 means Muted, then your original script's `state` maps 0.0 to True (muted).
        # And `mute_booleans['mic6'] = not state.get(6, True)` implies `mute_booleans['mic6']` is True if channel 6 is NOT muted.
        # This seems counter-intuitive to "mute_booleans".
        # Let's align `mute_booleans` to be TRUE when something IS muted.

        # Correcting the interpretation based on common X32 OSC behavior:
        # /ch/XX/mix/on 1.0 -> channel is ON (unmuted)
        # /ch/XX/mix/on 0.0 -> channel is OFF (muted)
        # Your script: `muted = (msg.params[0] == 0.0)` sets `muted` to True if it receives 0.0 (meaning muted).
        # And `state[ch] = muted` means `state[ch]` is True if the channel is muted.
        # Then `mute_booleans['mic6'] = not state.get(6, True)` means `mute_booleans['mic6']` is TRUE if channel 6 is NOT muted.
        # This is opposite of what "mute_booleans" would suggest.

        # Let's redefine `mute_booleans` to be TRUE when something IS MUTED.
        # The `state` variable correctly holds TRUE if muted based on `msg.params[0] == 0.0`.
        mute_booleans['mic6'] = state.get(6, False)  # True if channel 6 is muted
        mute_booleans['mic7'] = state.get(7, False)  # True if channel 7 is muted
        mute_booleans['mic8'] = state.get(8, False)  # True if channel 8 is muted

        for group, chans in GROUP_CHANNELS.items():
            if group == 'Handheld':
                # Handheld group is considered "muted" if ANY of its channels are muted.
                mute_booleans[group] = any(state.get(ch, False) for ch in chans)
            else:
                # Other groups are considered "muted" if ALL of its channels are muted.
                mute_booleans[group] = all(state.get(ch, False) for ch in chans)

        mute_booleans['dca6'] = state.get("dca6", False) # True if DCA 6 is muted
        mute_booleans['dca7'] = state.get("dca7", False) # True if DCA 7 is muted
        mute_booleans['dca8'] = state.get("dca8", False) # True if DCA 8 is muted

def handle_incoming(data):
    """
    Handles incoming OSC messages that are not meter blobs, specifically for mute status.
    This updates `state` and then `mute_booleans`.
    """
    packet = OscPacket(data)
    updated = False
    with lock:
        for raw in packet.messages:
            msg = unwrap_msg(raw)
            addr = msg.address
            if "/ch/" in addr and "/mix/on" in addr:
                ch = int(addr.split("/")[2])
                # msg.params[0] is 0.0 for muted, 1.0 for unmuted.
                # So, state[ch] will be True if muted, False if unmuted.
                muted = (msg.params[0] == 0.0)
                prev = state.get(ch)
                state[ch] = muted
                if prev is None or prev != muted:
                    updated = True
            elif "/dca/" in addr and "/on" in addr:
                dca = int(addr.split("/")[2])
                key = f"dca{dca}"
                # state[key] will be True if muted, False if unmuted.
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
        # Poll individual channels and all channels in groups
        for ch in INDIVIDUAL_CHANNELS + sum(GROUP_CHANNELS.values(), []):
            sock.sendto(build_poll(ch), (X32_IP, X32_PORT))
        for dca in DCAS:
            sock.sendto(build_dca_poll(dca), (X32_IP, X32_PORT))
        time.sleep(POLL_SEC)

# ——— Main Execution ———
if __name__ == "__main__":
    # Start the X32 communication and data updating in a separate thread
    threading.Thread(target=osc_loop, daemon=True).start()

    # Start the L-indicator GUI update loop
    # This also calls evaluate_indicator_states within its refresh cycle
    root.after(100, update_L_display)
    # The `update_L_display` function will be responsible for calling `evaluate_indicator_states`
    # or ensuring it's called frequently enough. Let's make sure it is.
    # We will call evaluate_indicator_states directly after `evaluate_levels` and `handle_incoming`
    # to ensure the `states_L` are updated as soon as the `low_status` or `mute_booleans` change.

    root.mainloop()