"""Retired configuration converter; retained only to fail old invocations clearly."""
import sys


def main():
    print('Legacy configuration conversion is retired. Configure life using config.example.toml; no files were changed.',
          file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
