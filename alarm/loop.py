import asyncio
import curses
import threading
from datetime import datetime
from pathlib import Path

import geocoder
from PIL import Image, ImageDraw, ImageFont

from alarm.control import *
from alarm.weather import get_weather

DHT_UPDATE_INTERVAL = 5
WEATHER_UPDATE_INTERVAL = 1800
USE_PURDUE_LOCATION = True

now = datetime.now()
dht_sensor = DHT11Sensor()
temp_hum_lock = threading.Lock()
weather_lock = threading.Lock()

weather = None
temp, hum = 0, 0


def alarm_loop(stdscr: curses.window):

    location = (
        (geocoder.ip("me").latlng) if not USE_PURDUE_LOCATION else [40.4237, -86.9212]
    )
    stdscr.addstr(f"Location: {location}\n")

    display = AlarmDisplay(25, 24)
    display.clear_screen()

    time_img = Image.new("1", (display.WIDTH, display.HEIGHT), 0)
    img_draw = ImageDraw.Draw(time_img)
    big_font = ImageFont.truetype(Path("./alarm/assets/bedstead.regular.otf"), 22)
    small_font = ImageFont.truetype(Path("./alarm/assets/bedstead.regular.otf"), 10)

    buttons: dict[str, AlarmButton] = {
        "enable_alarm": AlarmButton(22),
        "select": AlarmButton(27),
        "right": AlarmButton(4),
        "left": AlarmButton(21),
    }

    buzzer_r = AlarmBuzzer(26)
    buzzer_l = AlarmBuzzer(17)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stdscr.nodelay(True)
    stdscr.addstr(f"Raspi Alarm\nStarted {now_str}\n\nPress Enter to exit...\n")
    dht_thread = threading.Thread(target=update_dht, daemon=True)
    weather_thread = threading.Thread(
        target=update_weather, args=(location,), daemon=True
    )
    dht_thread.start()
    weather_thread.start()

    song_thread = threading.Thread(
        target=buzzer_r.play_melody,
        args=(Path("./alarm/assets/tetris.mid"),),
        daemon=True,
    )
    song_thread.start()

    my_temp, my_hum = 0, 0
    my_weather = None

    while True:
        for b in buttons.values():
            b.update_press()

        if buttons["select"].just_pressed:
            buzzer_r.stop()
            if song_thread.is_alive():
                song_thread.join()
            else:
                song_thread = threading.Thread(
                    target=buzzer_r.play_melody,
                    args=(Path("./alarm/assets/tetris.mid"),),
                    daemon=True,
                )
                song_thread.start()

        # Get Time
        now = datetime.now()
        hour, minute, second, microsecond = (
            now.hour,
            now.minute,
            now.second,
            now.microsecond,
        )
        am_pm = "AM" if hour < 12 else "PM"
        hour = hour % 12
        hour = 12 if hour == 0 else hour
        colon_on = (microsecond // 500000) % 2 == 0
        time_str = f"{hour}{':' if colon_on else ' '}{minute:02}"

        # Display Time
        img_draw.rectangle((0, 0, display.WIDTH, display.HEIGHT), outline=0, fill=0)
        time_len = int(img_draw.textlength(time_str, big_font))
        img_draw.text((time_len + 3, 0), am_pm, 1, small_font)
        img_draw.text((time_len + 3, 10), f"{second:02}", 1, small_font)
        img_draw.text((0, 0), time_str, 1, big_font)

        # Display Date
        date_str = now.strftime("%a, %b %d")
        img_draw.text((0, 20), date_str, 1, small_font)

        # Display Temp/Hum
        if temp_hum_lock.acquire(blocking=False):
            my_temp, my_hum = temp, hum
            temp_hum_lock.release()
        temp_len = int(img_draw.textlength(f"{my_temp}°F", small_font))
        hum_len = int(img_draw.textlength(f"{my_hum}%", small_font))
        img_draw.text((display.WIDTH - temp_len, 0), f"{my_temp}°F", 1, small_font)
        img_draw.text((display.WIDTH - hum_len, 10), f"{my_hum}%", 1, small_font)

        # Display Weather
        if weather_lock.acquire(blocking=False):
            my_weather = weather
            weather_lock.release()
        if my_weather is not None:
            weather_str = f"{my_weather.temperature}°F"
            weather_len = int(img_draw.textlength(weather_str, small_font))
            img_draw.text((display.WIDTH - weather_len, 20), weather_str, 1, small_font)

        display.write_image(time_img)

        # Terminal Output
        stdscr.addstr(6, 0, f"Time: {now.strftime('%I:%M:%S %p')}\n")
        stdscr.addstr(
            7,
            0,
            (
                f"Weather: {my_weather.location}\n"
                if my_weather is not None
                else "Weather: N/A\n"
            ),
        )
        try:
            key = stdscr.getkey()
            if key == "\n":
                break
        except curses.error:
            pass

        time.sleep(0.05)

    if song_thread.is_alive():
        song_thread.join()
    dht_sensor.sensor.exit()
    display.close()


def update_dht():
    global dht_sensor, temp, hum
    while True:
        t, h = dht_sensor.read()
        if t is not None and h is not None:
            with temp_hum_lock:
                temp, hum = round(c_to_f(t)), round(h)
        time.sleep(DHT_UPDATE_INTERVAL)


def update_weather(location: tuple[float, float]):
    global weather
    while True:
        new_weather = asyncio.run(get_weather(location))
        if new_weather is not None:
            with weather_lock:
                weather = new_weather
        time.sleep(WEATHER_UPDATE_INTERVAL)


def c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0
