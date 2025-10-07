import curses

from alarm.loop import alarm_loop


def main():
    curses.wrapper(alarm_loop)


if __name__ == "__main__":
    main()
