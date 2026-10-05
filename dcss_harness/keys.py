"""Native command aliases and key-name parsing."""




KEYS = {"Enter": 13, "Escape": 27, "Tab": 9, "Space": 32,
        "Backspace": 8}


DIRECTIONS = {"n": "k", "ne": "u", "e": "l", "se": "n",
              "s": "j", "sw": "b", "w": "h", "nw": "y"}


ACTIONS = {"wait": ".", "explore": "o", "rest": "5", "inventory": "i",
           "pickup": ",", "up": "<", "down": ">", "save": "\x13"}


def keycode(name):
    if name in KEYS:
        return KEYS[name]
    if name.startswith("Ctrl-") and len(name) == 6:
        return ord(name[-1].upper()) & 31
    if len(name) == 1:
        return ord(name)
    raise ValueError("Use a single character, Enter, Escape, Tab, Space, or Ctrl-X")
