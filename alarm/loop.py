import asyncio
import curses
from datetime import datetime

from alarm.control import AlarmButton

UPDATE_S = 0.2

now = datetime.now()


def entry(stdscr: curses.window):
    asyncio.run(alarm_loop(stdscr))


async def alarm_loop(stdscr: curses.window):
    buttons: list[AlarmButton] = []

    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    stdscr.nodelay(True)
    stdscr.addstr(f"Raspi Alarm\nStarted {now_str}\n\nPress Enter to exit...\n")
    update_task = asyncio.create_task(update_time())

    while True:
        for b in buttons:
            b.update_press()

        stdscr.addstr(5, 0, f"Time: {now.strftime('%H:%M:%S')}\n")
        try:
            key = stdscr.getkey()
            if key == "\n":
                break
        except curses.error:
            pass

        # Required to allow other tasks to run
        await asyncio.sleep(0)

    update_task.cancel()


async def update_time():
    global now
    while True:
        now = datetime.now()
        await asyncio.sleep(UPDATE_S)
