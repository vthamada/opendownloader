"""Small, independent installer/uninstaller backend entry point."""
import sys
from lume.maintenance import main

if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
