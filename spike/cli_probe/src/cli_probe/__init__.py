"""THROWAWAY (plan 5 cycle 0): report cwd, isatty and whether /dev/tty opens."""
import os, sys

def run():
    try:
        os.close(os.open("/dev/tty", os.O_RDWR)); tty = "opens"
    except OSError as e:
        tty = f"fails ({e.strerror})"
    print(f"cwd={os.getcwd()} stdin.isatty={sys.stdin.isatty()} stdout.isatty={sys.stdout.isatty()} /dev/tty={tty}")
