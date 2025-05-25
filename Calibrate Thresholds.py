import socket
import struct
import time
import re
from decimal import Decimal, getcontext
from pythonosc.osc_message_builder import OscMessageBuilder

# Configuration
X32_IP = '192.168.3.110'
X32_PORT = 10023
LOCAL_PORT = 10025
SUBSCRIPTION_NAME = 'mtrs'
METERS_PATH = '/meters/1'
SCRIPT_PATH = 'OSC Update1R.py'  # Path to the target script

CHANNELS_TO_CALIBRATE = list(range(3, 17))
COLLECTION_DURATION = 3  # seconds

getcontext().prec = 12  # For 10 decimal places

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

def collect_levels(state):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', LOCAL_PORT))
    send_osc_message(sock, '/batchsubscribe', 'ssiii', [SUBSCRIPTION_NAME, METERS_PATH, 0, 0, 0])
    buffer = {ch: [] for ch in CHANNELS_TO_CALIBRATE}
    print(f"Collecting values with mics {state}...")

    end_time = time.time() + COLLECTION_DURATION
    sock.settimeout(0.5)

    while time.time() < end_time:
        try:
            data, _ = sock.recvfrom(4096)
            if len(data) > 225:
                values = parse_x32_meter_blob(data)
                for ch in CHANNELS_TO_CALIBRATE:
                    if ch - 1 < len(values):
                        buffer[ch].append(values[ch - 1])
        except socket.timeout:
            continue

    sock.close()
    return {
        ch: max(buffer[ch]) if state == "off" else min(buffer[ch]) if buffer[ch] else 0.0
        for ch in CHANNELS_TO_CALIBRATE
    }

def generate_thresholds(mins, maxs):
    thresholds = {}
    for ch in CHANNELS_TO_CALIBRATE:
        low = Decimal(str(min(mins[ch], maxs[ch])))
        high = Decimal(str(max(mins[ch], maxs[ch])))
        mid = low + (high - low) / 2
        thresholds[ch] = float(mid.quantize(Decimal('0.0000000001')))
    return thresholds

def format_thresholds(thresh):
    lines = []
    lines.append("THRESHOLDS = {")
    for ch in CHANNELS_TO_CALIBRATE:
        comment = ""
        if ch in [3, 4, 5]: comment = "  # Instrumental"
        elif ch == 6: comment = "  # Lapel"
        elif ch == 7: comment = "  # Pulpit"
        elif ch == 8: comment = "  # White"
        elif ch in [9, 10, 11, 12]: comment = "  # Handheld"
        elif ch in [13, 14, 15, 16]: comment = "  # Choir"
        comma = "," if ch != 16 else ""
        lines.append(f"    {ch}: {thresh[ch]:.10f}{comma}{comment}")
    lines.append("}")
    return "\n".join(lines)

def update_main_script(new_thresholds_block):
    with open(SCRIPT_PATH, 'r') as f:
        lines = f.readlines()

    start_line = None
    end_line = None
    for i, line in enumerate(lines):
        if re.match(r'^\s*THRESHOLDS\s*=\s*{', line):
            start_line = i
        elif start_line is not None and line.strip().endswith("}"):
            end_line = i
            break

    if start_line is not None and end_line is not None:
        updated_lines = new_thresholds_block.splitlines(keepends=True)
        updated_lines = [line + '\n' if not line.endswith('\n') else line for line in updated_lines]
        lines[start_line:end_line + 1] = updated_lines
        with open(SCRIPT_PATH, 'w') as f:
            f.writelines(lines)
        print(f"\n✅ Updated thresholds in '{SCRIPT_PATH}' successfully.\n")
    else:
        print("❌ Could not locate THRESHOLDS block in main script.")

def calibrate():
    input("🔌 Unplug/turn off all microphones, then press Enter to begin max level capture...")
    max_levels = collect_levels("off")

    input("🎙️ Now plug in/turn on all microphones, then press Enter to begin min level capture...")
    min_levels = collect_levels("on")

    thresholds = generate_thresholds(min_levels, max_levels)
    threshold_block = format_thresholds(thresholds)
    print("\nGenerated Thresholds:\n")
    print(threshold_block)
    update_main_script(threshold_block)

if __name__ == "__main__":
    calibrate()