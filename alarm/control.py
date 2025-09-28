from gpiozero import Button


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
