import curses

from alarm.loop import entry


def main():
    curses.wrapper(entry)


if __name__ == "__main__":
    main()
