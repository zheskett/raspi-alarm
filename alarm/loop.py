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

DHT_UPDATE_INTERVAL = 8
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

    @classmethod
    def wrap(cls, value: int):
        range_size = cls.max() - cls.min() + 1
        return ((value - cls.min()) % range_size) + cls.min()


class MainPos(CursorPos):
    NO_SELECTION = 0
    SETTINGS = 1
    SET_ALARM_TIME = 2


class SettingsPos(CursorPos):
    SET_BRIGHTNESS = 0
    DISPLAY_OFF = 1
    BACK = 2


class AlarmTimePos(CursorPos):
    HOUR = 0
    MINUTE = 1
    AM_PM = 2
    BACK = 3


now = datetime.now()
dht_sensor = DHT11Sensor()
temp_hum_lock = threading.Lock()
weather_lock = threading.Lock()

weather = None
temp, hum = 0, 0


def alarm_loop(stdscr: curses.window):
    song_start_time = None
    has_reset_screen = False
    alarm_played_recently = False
    alarm_active = True

    my_temp, my_hum = 0, 0
    my_weather = None

    state = MenuState.MAIN
    cursor = MainPos.NO_SELECTION
    in_select_mode = False

    alarm_time = (10, 00, "AM")  # hour, minute, am/pm

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
    beep_tone = Tone("A5")
    beep_thread = threading.Thread(
        target=secondary_buzzer.thread_play_beep,
        args=(beep_tone,),
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
                menu_press = False

                if state == MenuState.MAIN:
                    if cursor != MainPos.NO_SELECTION:
                        state = (
                            MenuState.SETTINGS
                            if cursor == MainPos.SETTINGS
                            else MenuState.SET_ALARM_TIME
                        )
                        cursor = pos_class(state).min()
                        menu_press = True

                elif state == MenuState.SETTINGS:
                    menu_press = True
                    if cursor == SettingsPos.BACK:
                        state = MenuState.MAIN
                        cursor = MainPos.NO_SELECTION
                    elif (
                        cursor == SettingsPos.SET_BRIGHTNESS
                        or cursor == SettingsPos.DISPLAY_OFF
                    ):
                        in_select_mode = not in_select_mode

                elif state == MenuState.SET_ALARM_TIME:
                    menu_press = True
                    if cursor == AlarmTimePos.BACK:
                        state = MenuState.MAIN
                        cursor = MainPos.NO_SELECTION
                    else:
                        in_select_mode = not in_select_mode

                if menu_press:
                    secondary_buzzer.stop()
                    # Only join if thread was started in the first place
                    if beep_thread.ident is not None:
                        beep_thread.join()
                    beep_thread = threading.Thread(
                        target=secondary_buzzer.thread_play_beep,
                        args=(beep_tone,),
                        daemon=True,
                    )
                    beep_thread.start()

        if buttons["right"].just_pressed:
            if not in_select_mode:
                if state == MenuState.MAIN:
                    cursor = MainPos.wrap(cursor + 1)
                else:
                    cursor = pos_class(state).clamp(cursor + 1)
            else:
                if state == MenuState.SETTINGS:
                    if cursor == SettingsPos.SET_BRIGHTNESS:
                        new_dim = display.get_dim_level() + 1
                        display.set_dim_level(new_dim)

                elif state == MenuState.SET_ALARM_TIME:
                    if cursor == AlarmTimePos.HOUR:
                        hour = (alarm_time[0] + 1) % 12
                        hour = 12 if hour == 0 else hour
                        alarm_time = (hour, alarm_time[1], alarm_time[2])
                    elif cursor == AlarmTimePos.MINUTE:
                        minute = (alarm_time[1] + 1) % 60
                        alarm_time = (alarm_time[0], minute, alarm_time[2])
                    elif cursor == AlarmTimePos.AM_PM:
                        alarm_time = (
                            alarm_time[0],
                            alarm_time[1],
                            "PM" if alarm_time[2] == "AM" else "AM",
                        )

        if buttons["left"].just_pressed:
            if not in_select_mode:
                if state == MenuState.MAIN:
                    cursor = MainPos.wrap(cursor - 1)
                else:
                    cursor = pos_class(state).clamp(cursor - 1)
            else:
                if state == MenuState.SETTINGS:
                    if cursor == SettingsPos.SET_BRIGHTNESS:
                        new_dim = display.get_dim_level() - 1
                        display.set_dim_level(new_dim)

                elif state == MenuState.SET_ALARM_TIME:
                    if cursor == AlarmTimePos.HOUR:
                        hour = (alarm_time[0] - 1) % 12
                        hour = 12 if hour == 0 else hour
                        alarm_time = (hour, alarm_time[1], alarm_time[2])
                    elif cursor == AlarmTimePos.MINUTE:
                        minute = (alarm_time[1] - 1) % 60
                        alarm_time = (alarm_time[0], minute, alarm_time[2])
                    elif cursor == AlarmTimePos.AM_PM:
                        alarm_time = (
                            alarm_time[0],
                            alarm_time[1],
                            "PM" if alarm_time[2] == "AM" else "AM",
                        )

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
        blink_state = (microsecond // 500000) % 2 == 0
        time_str = f"{hour}{':' if blink_state else ' '}{minute:02}"

        # Draw Screen
        img_draw.rectangle((0, 0, display.WIDTH, display.HEIGHT), outline=0, fill=0)
        if state == MenuState.MAIN:
            # Display Time
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
                img_draw.bitmap((112, 32), bell_on_icon, fill=1)
            else:
                img_draw.bitmap((112, 32), bell_off_icon, fill=1)

            # Display Pages on Bottom
            if cursor != MainPos.NO_SELECTION:
                page_top = 52
                page_bottom = 63
                page_width = display.WIDTH // MainPos.max()
                for i in range(0, MainPos.max()):
                    x_start = i * page_width
                    x_end = x_start + page_width - 1
                    fill = 1 if (i + 1) == cursor else 0
                    img_draw.rectangle(
                        (x_start, page_top, x_end, page_bottom), fill=fill, outline=1
                    )
                    text = "Settings" if (i + 1) == MainPos.SETTINGS else "Set Alarm"
                    text_len = int(img_draw.textlength(text, small_font))
                    img_draw.text(
                        (x_start + (page_width - text_len) // 2, page_top + 1),
                        text,
                        0 if fill == 1 else 1,
                        small_font,
                    )

        elif state == MenuState.SETTINGS:
            img_draw.text((0, 0), "Settings", 1, big_font)

            setting_items = ["Set Brightness", "Display Off", "Back"]
            for i, item in enumerate(setting_items):
                y_pos = 28 + i * 12
                prefix = "> " if cursor == i else "  "
                if i != cursor or not in_select_mode or blink_state:
                    img_draw.text((0, y_pos), f"{prefix}{item}", 1, small_font)

        elif state == MenuState.SET_ALARM_TIME:
            # Display Alarm Time
            alarm_hour, alarm_minute, alarm_am_pm = alarm_time
            alarm_time_str = f"{alarm_hour}:{alarm_minute:02} {alarm_am_pm}"
            alarm_time_len = int(img_draw.textlength(alarm_time_str, big_font))
            img_draw.text(
                ((display.WIDTH - alarm_time_len) // 2, 21), alarm_time_str, 1, big_font
            )

            # Display Cursor
            y_pos = 44
            x_positions = [
                (display.WIDTH - alarm_time_len) // 2,
                (display.WIDTH - alarm_time_len) // 2
                + int(img_draw.textlength(f"{alarm_hour}:", big_font)),
                (display.WIDTH + alarm_time_len) // 2
                - int(img_draw.textlength(alarm_am_pm, big_font)),
            ]
            if (blink_state or not in_select_mode) and cursor != AlarmTimePos.BACK:
                x_pos = x_positions[cursor]
                cursor_width = (
                    img_draw.textlength("  ", big_font)
                    if cursor != AlarmTimePos.HOUR
                    else img_draw.textlength(f"{alarm_hour}", big_font)
                )
                img_draw.rectangle(
                    (x_pos - 1, y_pos, x_pos + cursor_width, y_pos + 4), fill=1
                )
            elif cursor == AlarmTimePos.BACK:
                back_text = "Back"
                back_len = int(img_draw.textlength(back_text, small_font))
                img_draw.text(
                    (
                        (display.WIDTH - back_len) // 2,
                        display.HEIGHT - 10,
                    ),
                    back_text,
                    1,
                    small_font,
                )

        # Every SCREEN_RESET_INTERVAL_MIN minutes, reset the screen
        if now.minute % SCREEN_RESET_INTERVAL_MIN == 0 and not has_reset_screen:
            display.reinitialize()
            has_reset_screen = True
        elif now.minute % SCREEN_RESET_INTERVAL_MIN != 0:
            has_reset_screen = False

        # Update Display
        if (
            state == MenuState.SETTINGS
            and cursor == SettingsPos.DISPLAY_OFF
            and in_select_mode
        ):
            display.clear_screen()
        else:
            display.write_image(time_img)

        # Do alarm
        if (
            alarm_active
            and (hour, minute, am_pm) == alarm_time
            and not alarm_played_recently
            and state != MenuState.SET_ALARM_TIME
        ):
            if not song_thread.is_alive():
                song_thread = threading.Thread(
                    target=main_buzzer.play_melody,
                    args=(SOUND_PATH,),
                    daemon=True,
                )
                song_thread.start()
            alarm_played_recently = True
            song_start_time = now
        elif (hour, minute, am_pm) != alarm_time:
            alarm_played_recently = False
            # Stop alarm after 15 minutes
            if song_start_time and (now - song_start_time).total_seconds() > 15 * 60 and song_thread.is_alive():
                main_buzzer.stop()
                song_thread.join()
                song_start_time = None

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
        for b_name, b in buttons.items():
            stdscr.addstr(
                9 + list(buttons.keys()).index(b_name), 0, f"{b_name}: {b.down}\n"
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


def pos_class(state: MenuState):
    return (
        MainPos
        if state == MenuState.MAIN
        else SettingsPos if state == MenuState.SETTINGS else AlarmTimePos
    )
