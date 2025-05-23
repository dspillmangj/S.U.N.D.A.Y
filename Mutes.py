import socket
import threading
import time
from pythonosc.osc_message_builder import OscMessageBuilder
from pythonosc.osc_packet import OscPacket

# ——— CONFIGURATION —————————————————————————————————————————
X32_IP      = "192.168.3.110"
X32_PORT    = 10023
LOCAL_PORT  = 10024
POLL_SEC    = 0.01

# Channels to monitor
INDIVIDUAL_CHANNELS = [6, 7, 8]
GROUP_CHANNELS = {
    'group1': [3, 4, 5],
    'group2': [9, 10, 11, 12],
    'group3': [13, 14, 15, 16]
}
DCAS = [6, 7, 8]
# ————————————————————————————————————————————————————————

# state tracking
state = {}
lock = threading.Lock()

# Boolean outputs
booleans = {
    'mic6': False,
    'mic7': False,
    'mic8': False,
    'group1': False,
    'group2': False,
    'group3': False,
    'dca6': False,
    'dca7': False,
    'dca8': False
}


def send_xremote(sock):
    msg = OscMessageBuilder(address="/xremote").build().dgram
    sock.sendto(msg, (X32_IP, X32_PORT))
    time.sleep(0.1)


def build_poll(ch):
    return OscMessageBuilder(address=f"/ch/{ch:02}/mix/on").build().dgram


def build_dca_poll(dca_index):
    return OscMessageBuilder(address=f"/dca/{dca_index}/on").build().dgram


def poll_loop(sock):
    while True:
        # Poll channels
        for ch in INDIVIDUAL_CHANNELS + sum(GROUP_CHANNELS.values(), []):
            sock.sendto(build_poll(ch), (X32_IP, X32_PORT))
        # Poll DCAs
        for dca in DCAS:
            sock.sendto(build_dca_poll(dca), (X32_IP, X32_PORT))
        time.sleep(POLL_SEC)


def unwrap_msg(msg):
    return getattr(msg, 'message', msg)


def update_booleans():
    booleans['mic6'] = not state.get(6, True)
    booleans['mic7'] = not state.get(7, True)
    booleans['mic8'] = not state.get(8, True)

    for group, chans in GROUP_CHANNELS.items():
        booleans[group] = all(not state.get(ch, True) for ch in chans)

    booleans['dca6'] = not state.get("dca6", True)
    booleans['dca7'] = not state.get("dca7", True)
    booleans['dca8'] = not state.get("dca8", True)

    print("Boolean outputs:")
    for name, val in booleans.items():
        print(f"  {name}: {val}")
    print("---")


def handle_incoming(data):
    packet = OscPacket(data)
    updated = False
    with lock:
        for raw in packet.messages:
            msg = unwrap_msg(raw)
            addr = msg.address

            # Channel mute state
            if "/ch/" in addr and "/mix/on" in addr:
                ch = int(addr.split("/")[2])
                muted = (msg.params[0] == 0.0)
                prev = state.get(ch)
                state[ch] = muted
                if prev is None or prev != muted:
                    updated = True

            # DCA mute state
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


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", LOCAL_PORT))
    print(f"Bound to UDP port {LOCAL_PORT}")

#    send_xremote(sock)
#    print("Sent /xremote → X32 (remote mode)")

    threading.Thread(target=poll_loop, args=(sock,), daemon=True).start()
    print(f"Polling channels and DCAs every {POLL_SEC}s…")

    try:
        while True:
            data, _ = sock.recvfrom(2048)
            handle_incoming(data)
    except KeyboardInterrupt:
        print("\nStopping monitor.")


if __name__ == "__main__":
    main()