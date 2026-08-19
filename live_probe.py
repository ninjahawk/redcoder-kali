#!/usr/bin/env python3
"""Throwaway probe: does this terminal support a scroll-region-pinned bottom bar?
Run it, watch: 'agent output' lines should scroll in the UPPER area while the boxed bar
stays pinned at the very bottom the whole time. Ctrl-C is fine. It resets the terminal on exit.
"""
import sys, time, shutil

def w(s): sys.stdout.write(s); sys.stdout.flush()

cols, rows = shutil.get_terminal_size((80, 24))
BAR_H = 3                                   # bottom rows reserved for the bar
top_last = rows - BAR_H                      # last scrollable row

def draw_bar(msg):
    w("\x1b7")                               # save cursor
    w(f"\x1b[{top_last+1};1H\x1b[K" + "\x1b[90m╭" + "─" * (cols - 2) + "╮\x1b[0m")
    w(f"\x1b[{top_last+2};1H\x1b[K" + "\x1b[90m│\x1b[0m \x1b[32m›\x1b[0m " + msg)
    w(f"\x1b[{top_last+3};1H\x1b[K" + "\x1b[90m╰" + "─" * (cols - 2) + "╯\x1b[0m")
    w("\x1b8")                               # restore cursor

try:
    w("\x1b[2J\x1b[H")                        # clear
    w(f"\x1b[1;{top_last}r")                  # scroll region = rows 1..top_last
    w(f"\x1b[{top_last};1H")                  # cursor to bottom of scroll region
    draw_bar("PINNED BAR — should stay here while text scrolls above")
    for i in range(30):
        w(f"\x1b[{top_last};1H")              # ensure we print at region bottom
        print(f"agent output line {i:2d}  ── this text should scroll ABOVE the bar", flush=True)
        draw_bar(f"PINNED BAR — still here (line {i})")
        time.sleep(0.12)
finally:
    w("\x1b[r")                              # reset scroll region (whole screen)
    w(f"\x1b[{rows};1H\n")
    print("PROBE DONE. Did the boxed bar stay pinned at the very bottom the entire time,")
    print("with the 'agent output' lines scrolling above it? (yes / no + what you saw)")
