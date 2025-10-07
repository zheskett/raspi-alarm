from dataclasses import dataclass
from pathlib import Path
from typing import cast

import mido
from gpiozero import TonalBuzzer
from gpiozero.tones import Tone
from mido import MidiFile


@dataclass
class Note:
    pitch: Tone | None
    duration: float


class Melody:
    def __init__(self, path: Path):
        self.path = path
        self.notes: list[Note] = []
        self.tempo = 500000

        mid = MidiFile(path, clip=True)
        track: mido.MidiTrack = cast(mido.MidiTrack, mid.tracks[0])
        current_note = None
        for msg in track:
            if msg.type == "set_tempo":
                self.tempo = msg.tempo
            elif msg.type == "note_on" and msg.velocity > 0:
                duration = mido.tick2second(msg.time, mid.ticks_per_beat, self.tempo)
                if current_note is not None:
                    self.notes.append(Note(current_note, duration))
                else:
                    if duration > 0:
                        self.notes.append(Note(None, duration))

                current_note = Tone(midi=msg.note)
            elif (msg.type == "note_off") or (
                msg.type == "note_on" and msg.velocity == 0
            ):
                if current_note is not None:
                    duration = mido.tick2second(
                        msg.time, mid.ticks_per_beat, self.tempo
                    )
                    self.notes.append(Note(current_note, duration))
                    current_note = None
