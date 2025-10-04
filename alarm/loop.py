import asyncio
import curses
import time
from datetime import datetime

from alarm.control import *

UPDATE_S = 0.2

now = datetime.now()
dht_sensor = DHT11Sensor()
temp, hum = 0.0, 0.0


def entry(stdscr: curses.window):
    asyncio.run(alarm_loop(stdscr))


async def alarm_loop(stdscr: curses.window):
    display = AlarmDisplay(25, 24)
    display.clear_screen()

    time_img = Image.new("1", (display.WIDTH, display.HEIGHT), 0)
    img_draw = ImageDraw.Draw(time_img)
    big_font = ImageFont.load_default(32)
    small_font = ImageFont.load_default(12)

    buttons: dict[str, AlarmButton] = {
        "enable_alarm": AlarmButton(22),
        "select": AlarmButton(27),
        "right": AlarmButton(4),
        "left": AlarmButton(21),
    }

    buzzer_r = AlarmBuzzer(26)
    buzzer_l = AlarmBuzzer(17)

    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    stdscr.nodelay(True)
    stdscr.addstr(f"Raspi Alarm\nStarted {now_str}\n\nPress Enter to exit...\n")
    update_task = asyncio.create_task(update_time())
    dht_task = asyncio.create_task(update_dht())

    while True:
        for b in buttons.values():
            b.update_press()

        time_str = now.strftime("%H:%M:%S")

        img_draw.rectangle((0, 0, display.WIDTH, display.HEIGHT), outline=0, fill=0)
        img_draw.text((0, -8), time_str, fill=1, font=big_font)
        img_draw.text((0, 50), f"T:{temp:.0f}°F H:{hum:.0f}%", fill=1, font=small_font)
        display.write_image(time_img)

        stdscr.addstr(5, 0, f"Time: {time_str}\n")
        try:
            key = stdscr.getkey()
            if key == "\n":
                break
        except curses.error:
            pass

        # Required to allow other tasks to run
        await asyncio.sleep(0.05)

    update_task.cancel()
    dht_task.cancel()
    dht_sensor.sensor.exit()
    display.close()


async def update_dht():
    global dht_sensor, temp, hum
    while True:
        t, h = dht_sensor.read()
        if t is not None and h is not None:
            temp, hum = c_to_f(t), h
        await asyncio.sleep(10.0)


async def update_time():
    global now
    while True:
        now = datetime.now()
        await asyncio.sleep(UPDATE_S)


def c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0
