"""Entry point: runs our squad brain on top of our core helpers."""
import core
from squad import Squad

core.Bot = Squad

if __name__ == "__main__":
    core.main()
