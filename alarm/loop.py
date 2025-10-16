import asyncio
import curses
import threading
from datetime import datetime
from enum import IntEnum, StrEnum, auto
from pathlib import Path

import geocoder
from PIL import Image, ImageDraw, ImageFont

from alarm.control import *
from alarm.weather import get_weather

DHT_UPDATE_INTERVAL = 5
WEATHER_UPDATE_INTERVAL = 1800
SCREEN_RESET_INTERVAL_MIN = 10  # in minutes
USE_PURDUE_LOCATION = True

SOUND_PATH = Path("./alarm/assets/tetris.mid")
FONT_PATH = Path("./alarm/assets/bedstead.regular.otf")


class MenuState(StrEnum):
    MAIN = auto()
    SETTINGS = auto()
    SET_ALARM_TIME = auto()


class CursorPos(IntEnum):
    @classmethod
    def max(cls):
        return max(item.value for item in cls)

    @classmethod
    def min(cls):
        return min(item.value for item in cls)

    @classmethod
    def clamp(cls, value: int):
        return max(cls.min(), min(cls.max(), value))


class MainPos(CursorPos):
    SETTINGS = 0
    SET_ALARM_TIME = 1


class SettingsPos(CursorPos):
    SET_BRIGHTNESS = 0
    BACK = 1


class AlarmTimePos(CursorPos):
    HOUR = 0
    MINUTE = 1
    AM_PM = 2


now = datetime.now()
dht_sensor = DHT11Sensor()
temp_hum_lock = threading.Lock()
weather_lock = threading.Lock()

weather = None
temp, hum = 0, 0


def alarm_loop(stdscr: curses.window):
    has_reset_screen = False
    alarm_played_recently = False
    alarm_active = True

    my_temp, my_hum = 0, 0
    my_weather = None

    state = MenuState.MAIN
    cursor = MainPos.SETTINGS

    alarm_time = (10, 11, "AM")  # hour, minute, am/pm

    location = (
        (lambda l: f"{l.city}, {l.state}")(geocoder.ip("me"))
        if not USE_PURDUE_LOCATION
        else "West Lafayette, Indiana"
    )
    stdscr.addstr(f"Location: {location}\n")

    display = AlarmDisplay(25, 24)
    display.clear_screen()

    time_img = Image.new("1", (display.WIDTH, display.HEIGHT), 0)
    img_draw = ImageDraw.Draw(time_img)
    big_font = ImageFont.truetype(FONT_PATH, 22)
    small_font = ImageFont.truetype(FONT_PATH, 10)
    bell_on_icon = Image.open(Path("./alarm/assets/bell_on.png")).convert("1")
    bell_off_icon = Image.open(Path("./alarm/assets/bell_off.png")).convert("1")

    buttons: dict[str, AlarmButton] = {
        "enable_alarm": AlarmButton(22),
        "select": AlarmButton(27),
        "right": AlarmButton(4),
        "left": AlarmButton(21),
    }

    main_buzzer = AlarmBuzzer(26)
    secondary_buzzer = AlarmBuzzer(17)

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
        target=main_buzzer.play_melody,
        args=(SOUND_PATH,),
        daemon=True,
    )

    while True:
        for b in buttons.values():
            b.update_press()

        # Handle Buttons
        if buttons["enable_alarm"].just_pressed:
            alarm_active = not alarm_active
            if not alarm_active and song_thread.is_alive():
                main_buzzer.stop()
                song_thread.join()
        if buttons["select"].just_pressed:
            if song_thread.is_alive():
                main_buzzer.stop()
                song_thread.join()
            else:
                song_thread = threading.Thread(
                    target=main_buzzer.play_melody,
                    args=(SOUND_PATH,),
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
            high = my_weather.daily_forecasts[0].highest_temperature
            low = my_weather.daily_forecasts[0].lowest_temperature
            high_low = f"H:{high}°/L:{low}°"
            try:
                # Hourly forecast is every 3 hours
                temp_str = f"{my_weather.daily_forecasts[0].hourly_forecasts[now.hour // 3].temperature}°F"
                img_draw.text((0, 30), temp_str, 1, small_font)
            except IndexError:
                pass
            img_draw.text((0, 40), high_low, 1, small_font)

        # Display Alarm Status
        if alarm_active:
            img_draw.bitmap((112, 48), bell_on_icon, fill=1)
        else:
            img_draw.bitmap((112, 48), bell_off_icon, fill=1)

        # Every SCREEN_RESET_INTERVAL_MIN minutes, reset the screen
        if now.minute % SCREEN_RESET_INTERVAL_MIN == 0 and not has_reset_screen:
            display.reinitialize()
            has_reset_screen = True
        elif now.minute % SCREEN_RESET_INTERVAL_MIN != 0:
            has_reset_screen = False

        # Update Display
        display.write_image(time_img)

        # Do alarm
        if (
            alarm_active
            and (hour, minute, am_pm) == alarm_time
            and not alarm_played_recently
        ):
            if not song_thread.is_alive():
                song_thread = threading.Thread(
                    target=main_buzzer.play_melody,
                    args=(SOUND_PATH,),
                    daemon=True,
                )
                song_thread.start()
            alarm_played_recently = True
        elif (hour, minute, am_pm) != alarm_time:
            alarm_played_recently = False

        # Terminal Output
        stdscr.addstr(6, 0, f"Time: {now.strftime('%I:%M:%S %p')}\n")
        stdscr.addstr(
            7,
            0,
            (
                f"Weather: {my_weather.daily_forecasts[0].highest_temperature}°F\n"
                if my_weather is not None
                else "Weather: N/A\n"
            ),
        )
        stdscr.addstr(8, 0, f"Alarm {'On ' if alarm_active else 'Off'}\n")
        for bname, b in buttons.items():
            stdscr.addstr(
                9 + list(buttons.keys()).index(bname), 0, f"{bname}: {b.down}\n"
            )
        try:
            key = stdscr.getkey()
            if key == "\n":
                break
        except curses.error:
            pass

        time.sleep(0.05)

    if song_thread.is_alive():
        main_buzzer.stop()
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
