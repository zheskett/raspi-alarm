import threading
import time
from pathlib import Path

import adafruit_dht
import board
import spidev
from gpiozero import Button, DigitalOutputDevice, TonalBuzzer
from gpiozero.tones import Tone
from PIL import Image

from alarm.audio import Melody

COMMAND_DICT: dict[str, int] = {
    "display_on": 0xAF,  # Turn the display on
    "display_off": 0xAE,  # Turn the display off
    "set_contrast": 0x81,  # Set contrast control (1 byte follow)
    "normal_display": 0xA6,  # Normal display (not inverted)
    "addressing_mode": 0x20,  # Set memory addressing mode (1 byte follow)
    "column_addr": 0x21,  # Set column address (2 bytes follow: start, end)
    "page_addr": 0x22,  # Set page address (2 bytes follow: start, end)
    "entire_display_on": 0xA5,  # Entire display ON (all pixels on)
    "use_ram": 0xA4,  # Resume to RAM content display
    "multiplex_ratio": 0xA8,  # Set multiplex ratio (1 byte follow: height-1)
    "display_offset": 0xD3,  # Set display offset (1 byte follow)
    "start_line_0": 0x40,  # Set display start line to 0
    "charge_pump": 0x8D,  # Charge pump setting (1 byte follow)
    "pre-charge_period": 0xD9,  # Set pre-charge period (1 byte follow)
    "VCOMH_deselect_level": 0xDB,  # Set VCOMH deselect level (1 byte follow)
    "segment_remap_mirror_x": 0xA1,  # Set segment remap (mirror X)
    "segment_remap_normal": 0xA0,  # Set segment remap (normal)
    "com_scan_dec": 0xC8,  # Set COM output scan direction (mirror Y)
    "com_scan_inc": 0xC0,  # Set COM output scan direction (normal)
    "com_pins": 0xDA,  # Set COM pins
    "osc_freq": 0xD5,  # Set oscillator frequency (1 byte follow)
}


class AlarmButton:
    """
    Represents a button connected to a GPIO pin.
    Tracks whether the button is currently pressed and if it was just pressed.

    Attributes:
        button (Button): The gpiozero Button instance.
        just_pressed (bool): True if the button was just pressed.
        down (bool): True if the button is currently pressed down.
    """

    def __init__(self, pin: int | str):
        self.button = Button(pin)
        self.just_pressed = False
        self.down = False

    def update_press(self):
        active = self.button.is_active
        if active and not self.down:
            self.just_pressed = True
        else:
            self.just_pressed = False

        self.down = active


class AlarmBuzzer:
    """
    Buzzer on a GPIO pin
    """

    def __init__(self, pin: int | str):
        self.tonal_buzzer = TonalBuzzer(pin, mid_tone=Tone(1200), octaves=3)
        self.tonal_buzzer.stop()
        self._play_lock = threading.Lock()
        self._stop = True

    def play_melody(self, melody: Path | Melody):
        """
        Play a melody from a Melody object.

        Should be called in a separate process and terminated.
        """
        melody = Melody(melody) if isinstance(melody, Path) else melody
        with self._play_lock:
            self._stop = False

        while True:
            for note in melody.notes:
                with self._play_lock:
                    if self._stop:
                        self.tonal_buzzer.stop()
                        return
                    if note.pitch is not None:
                        pitch = note.pitch
                        while pitch.midi < self.tonal_buzzer.min_tone.midi:
                            pitch = pitch.up(12)
                        while pitch.midi > self.tonal_buzzer.max_tone.midi:
                            pitch = pitch.down(12)
                        self.tonal_buzzer.play(pitch)
                    else:
                        self.tonal_buzzer.stop()
                time.sleep(note.duration * 0.8)
                with self._play_lock:
                    self.tonal_buzzer.stop()
                    if self._stop:
                        return
                time.sleep(note.duration * 0.2)

    def thread_play_beep(self, tone: Tone | int | float | str):
        tone_len_ms = 200
        tone_start_ns = 0
        with self._play_lock:
            self._stop = False
            self.tonal_buzzer.play(tone)
            tone_start_ns = time.time_ns()
        while True:
            with self._play_lock:
                if self._stop:
                    self.tonal_buzzer.stop()
                    return
            current_ns = time.time_ns()
            if current_ns - tone_start_ns >= tone_len_ms * 1e6:
                break
            time.sleep(0.01)
        self.stop()

    def stop(self):
        with self._play_lock:
            self.tonal_buzzer.stop()
            self._stop = True


class DHT11Sensor:
    """
    DHT11 temperature and humidity sensor on a GPIO pin
    """

    def __init__(self):
        self.sensor = adafruit_dht.DHT11(board.D19)

    def read(self) -> tuple[float | None, float | None]:
        try:
            self.sensor.measure()
        except RuntimeError:
            return None, None
        return self.sensor.temperature, self.sensor.humidity


class AlarmDisplay:
    """
    SSD1306 OLED screen on SPI
    128x64
    """

    WIDTH: int = 128
    HEIGHT: int = 64
    PAGES: int = HEIGHT // 8

    def __init__(self, dc_pin: int | str, rst_pin: int | str):
        self.spi = spidev.SpiDev()
        self.spi.open(0, 0)
        self.spi.max_speed_hz = 500_000
        self.spi.mode = 0b00
        self.dc = DigitalOutputDevice(dc_pin, initial_value=False)
        self.rst = DigitalOutputDevice(rst_pin, active_high=False, initial_value=False)
        self._dim = 0

        self.reinitialize()

    def exec_cmd(self, cmd: int, args: bytes = bytes()) -> None:
        self.dc.off()
        self.spi.writebytes2(bytes([cmd]) + args)

    def exec_data(self, data: bytes) -> None:
        self.dc.on()
        self.spi.writebytes2(data)

    def white_screen(self) -> None:
        self.exec_data(bytes([0xFF] * (self.WIDTH * self.PAGES)))

    def clear_screen(self) -> None:
        self.exec_data(bytes([0x00] * (self.WIDTH * self.PAGES)))

    def set_dim_level(self, level: int):
        level = max(0, min(1, level))

        self._dim = level

        if level == 0:
            self.exec_cmd(COMMAND_DICT["set_contrast"], bytes([0x00]))
            self.exec_cmd(COMMAND_DICT["pre-charge_period"], bytes([0x11]))
            self.exec_cmd(COMMAND_DICT["VCOMH_deselect_level"], bytes([0x20]))
        else:
            self.exec_cmd(COMMAND_DICT["set_contrast"], bytes([0x7F]))
            self.exec_cmd(COMMAND_DICT["pre-charge_period"], bytes([0xF1]))
            self.exec_cmd(COMMAND_DICT["VCOMH_deselect_level"], bytes([0x30]))

    def get_dim_level(self) -> int:
        return self._dim

    def write_image(self, image: Image.Image) -> None:
        if image.size != (self.WIDTH, self.HEIGHT):
            image = image.resize((self.WIDTH, self.HEIGHT), Image.Resampling.NEAREST)

        pixel_data = bytearray(list(image.convert("1").getdata(0)))
        buffer = bytearray(self.WIDTH * self.PAGES)
        for p in range(self.PAGES):
            for x in range(self.WIDTH):
                for page_col in range(8):
                    pixel = pixel_data[x + (p * 8 + page_col) * self.WIDTH]
                    buffer[x + p * self.WIDTH] |= pixel << page_col

        self.exec_data(buffer)

    def reset(self) -> None:
        self.rst.off()
        time.sleep(0.005)
        self.rst.on()
        time.sleep(0.005)
        self.rst.off()
        time.sleep(0.02)

    def reinitialize(self) -> None:
        self.reset()

        # region: Initialize commands
        self.exec_cmd(COMMAND_DICT["display_off"])
        self.exec_cmd(COMMAND_DICT["multiplex_ratio"], bytes([0x3F]))
        self.exec_cmd(COMMAND_DICT["display_offset"], bytes([0x00]))
        self.exec_cmd(COMMAND_DICT["start_line_0"])
        self.exec_cmd(COMMAND_DICT["segment_remap_normal"])
        self.exec_cmd(COMMAND_DICT["com_scan_inc"])
        self.exec_cmd(COMMAND_DICT["com_pins"], bytes([0x12]))
        self.exec_cmd(COMMAND_DICT["use_ram"])
        self.exec_cmd(COMMAND_DICT["normal_display"])
        self.exec_cmd(COMMAND_DICT["osc_freq"], bytes([0x80]))
        self.exec_cmd(COMMAND_DICT["charge_pump"], bytes([0x14]))
        self.set_dim_level(self._dim)
        self.exec_cmd(COMMAND_DICT["addressing_mode"], bytes([0x00]))
        self.exec_cmd(COMMAND_DICT["segment_remap_mirror_x"])
        self.exec_cmd(COMMAND_DICT["com_scan_dec"])
        self.exec_cmd(COMMAND_DICT["column_addr"], bytes([0x00, self.WIDTH - 1]))
        self.exec_cmd(COMMAND_DICT["page_addr"], bytes([0x00, self.PAGES - 1]))
        self.exec_cmd(COMMAND_DICT["display_on"])
        # endregion

    def close(self) -> None:
        self.exec_cmd(COMMAND_DICT["display_off"])
        self.exec_cmd(COMMAND_DICT["charge_pump"], bytes(0x10))
        self.spi.close()
        self.rst.close()
        self.dc.close()
