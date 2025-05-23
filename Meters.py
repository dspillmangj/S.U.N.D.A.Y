import decimal
from decimal import Decimal, ROUND_HALF_UP
import struct
import threading
import time
import socket

# Configuration
X32_IP = '192.168.3.110'  # Replace with your X32 mixer IP address
X32_PORT = 10023          # Default X32 OSC port
LOCAL_PORT = 10024        # Local port to listen for incoming messages
SUBSCRIPTION_NAME = 'mtrs'
METERS_PATH = '/meters/1'
RENEW_INTERVAL = 9        # Seconds between renewals

def send_osc_message(sock, address, types, args):
    """Send an OSC message to the X32 mixer."""
    # Build OSC address
    address_bytes = address.encode('utf-8')
    address_padded = address_bytes + b'\x00' * (4 - (len(address_bytes) % 4))

    # Build type tag string
    type_tags = ',' + types
    type_tags_bytes = type_tags.encode('utf-8')
    type_tags_padded = type_tags_bytes + b'\x00' * (4 - (len(type_tags_bytes) % 4))

    # Build arguments
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

    # Combine all parts
    message = address_padded + type_tags_padded + args_bytes
    sock.sendto(message, (X32_IP, X32_PORT))

def parse_x32_meter_blob(data):
    """
    Parses the OSC blob received from the X32 mixer and returns a list of float values rounded to four decimal places.
    """
    header_length = 12
    blob = data[header_length:]

    # The first 4 bytes of the blob indicate the size of the blob in bytes (big-endian)
    blob_size = struct.unpack('>I', blob[:4])[0]

    # The next 4 bytes indicate the number of float values (little-endian)
    num_values = struct.unpack('<I', blob[4:8])[0]

    # The remaining bytes are the float values (each 4 bytes, little-endian)
    float_data = blob[8:]

    # Ensure that the length of float_data matches the expected number of values
    expected_length = num_values * 4
    if len(float_data) < expected_length:
        raise ValueError("Incomplete float data in blob.")

    # Unpack the float values
    values = struct.unpack('<' + 'f' * num_values, float_data[:expected_length])

    # Round each value to four decimal places using the decimal module
    rounded_values = [
        float(Decimal(str(val)).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP))
        for val in values
    ]

    return rounded_values

def evaluate_levels(values):
    """
    Returns six boolean values indicating if any mic in each group is at or below the threshold.
    Index reference (0-based):
        Ch. 3-5  → values[2:5]     Instrumental
        Ch. 6    → values[5]       Lapel
        Ch. 7    → values[6]       Pulpit
        Ch. 8    → values[7]       White
        Ch. 9-12 → values[8:12]    Handheld
        Ch. 13-16→ values[12:16]   Choir
    """

    def any_below_or_equal(group, threshold):
        return any(val <= threshold for val in group)

    instrumental = any_below_or_equal(values[2:5], 0.0002)
    lapel        = values[5] <= 0.0000
    pulpit       = values[6] <= 0.0001
    white        = values[7] <= 0.0000
    handheld     = any_below_or_equal(values[8:12], 0.0000)
    choir        = any_below_or_equal(values[12:16], 0.0003)

    return instrumental, lapel, pulpit, white, handheld, choir

def receive_messages(sock):
    """Receive messages from the X32 mixer and evaluate channel levels."""
    while True:
        try:
            data, addr = sock.recvfrom(4096)
            values = parse_x32_meter_blob(data)
            levels_ok = evaluate_levels(values)

            print("Status (Instrumental, Lapel, Pulpit, White, Handheld, Choir):")
            print(levels_ok)
        except Exception as e:
            print(f"Error receiving or parsing data: {e}")
            break

def main():
    # Create UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('', LOCAL_PORT))

    # Start thread to receive messages
    threading.Thread(target=receive_messages, args=(sock,), daemon=True).start()

    # Send initial /batchsubscribe command
    send_osc_message(sock, '/batchsubscribe', 'ssiii', [SUBSCRIPTION_NAME, METERS_PATH, 0, 0, 0])
    print(f"Sent /batchsubscribe for {METERS_PATH}")

    # Periodically send /renew command
    try:
        while True:
            time.sleep(RENEW_INTERVAL)
            send_osc_message(sock, '/renew', 's', [SUBSCRIPTION_NAME])
            print(f"Sent /renew for {SUBSCRIPTION_NAME}")
    except KeyboardInterrupt:
        print("Exiting...")

if __name__ == '__main__':
    main()

# (Ch. 13-16) Choir Threshold - 0.0004 or below
# (Ch. 7) Pulpit Threshold - 0.0001 or below
# (Ch. 8) White Threshold - 0.0000
# (Ch. 9-12) Handheld Threshold - 0.000
# (Ch. 6) Lapel Threshold - 0.0000
# (Ch. 3-5) Instrumental Threshold - 0.0003 or below