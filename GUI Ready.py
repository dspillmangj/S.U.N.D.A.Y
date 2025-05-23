import tkinter as tk
from PIL import Image, ImageTk
import threading
import os

# CONFIGURATION
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

root = tk.Tk()
root.attributes("-topmost", True)
root.overrideredirect(True)

# Calculate layout based on selected monitor
image_width = monitor_width // 8
image_height = monitor_width // 16
root.geometry(f"{monitor_width}x{image_height}+{monitor_x}+{monitor_y}")
root.configure(bg='black')

# Load and resize image
def load_scaled_image(path, width, height):
    if not os.path.exists(path):
        print(f"Missing image: {path}")
        return None
    image = Image.open(path)
    image = image.resize((width, height), Image.LANCZOS)
    return ImageTk.PhotoImage(image)

# Load all indicator images
images = []
for i in range(1, 9):
    on = load_scaled_image(f"{i}I.png", image_width, image_height)
    off = load_scaled_image(f"{i}O.png", image_width, image_height)
    images.append({'on': on, 'off': off})

# GUI Labels and default indicator states
labels = []
states = ["off"] * 8  # Start all in 'off' state
for i in range(8):
    lbl = tk.Label(root, bg='black')
    lbl.place(x=i * image_width, y=0, width=image_width, height=image_height)
    labels.append(lbl)

# Flashing logic (synchronized)
flash_tick = 0  # Advances every 0.5 seconds

def update_display():
    global flash_tick
    flash_tick += 1
    flashon_state = flash_tick % 2 == 0              # Toggles every 0.5s
    flashoff_state = (flash_tick // 2) % 2 == 0       # Toggles every 1s

    for i in range(8):
        state = states[i]
        img = None

        if state == "on":
            img = images[i]['on']
        elif state == "off":
            img = images[i]['off']
        elif state == "flashon":
            img = images[i]['on'] if flashon_state else images[i]['off']
        elif state == "flashoff":
            img = images[i]['off'] if flashoff_state else None
        elif state == "blank":
            img = None

        if img:
            labels[i].config(image=img)
            labels[i].image = img
        else:
            labels[i].config(image='')
            labels[i].image = None

    root.after(500, update_display)  # Update every 0.5s (syncs all flashing)

# State update from console
def set_indicator(index, state):
    if 0 <= index < 8:
        if state in ["on", "off", "blank", "flashon", "flashoff"]:
            states[index] = state
        else:
            print("Invalid state. Use: on, off, blank, flashon, flashoff")
    else:
        print("Index must be between 0–7")

# Console input
def input_thread():
    while True:
        try:
            raw = input("Enter: <index> <on/off/flashon/flashoff/blank> → ")
            index_str, state = raw.strip().split()
            set_indicator(int(index_str), state.lower())
        except Exception as e:
            print("Error:", e)

# Start loop and console listener
root.after(0, update_display)
threading.Thread(target=input_thread, daemon=True).start()
root.mainloop()