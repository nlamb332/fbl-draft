import sys

from .cli import configure_stdout, main

if __name__ == "__main__":
    configure_stdout()
    sys.exit(main())
