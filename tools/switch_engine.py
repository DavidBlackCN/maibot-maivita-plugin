"""Retired engine switch command; never reads or changes configuration or data."""
import sys


MESSAGE = 'Engine switching is retired; only life is supported. No files were changed.'


def prepare(config_path, data_dir, target, output):
    raise RuntimeError(MESSAGE)


def main():
    print(MESSAGE, file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
