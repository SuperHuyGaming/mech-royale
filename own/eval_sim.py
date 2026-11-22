"""Run faithful_sim over N seeds for a bot module. usage: eval_sim.py <bot_dir> <n> [start]"""
import sys, os, importlib.util, statistics
KIT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kit", "mech")
bot_dir = os.path.abspath(sys.argv[1]); n = int(sys.argv[2]); start = int(sys.argv[3]) if len(sys.argv) > 3 else 1001
os.chdir(KIT); sys.path.insert(0, KIT)
import faithful_sim as fs
sys.path.insert(0, bot_dir)
for m in ("mechfight", "bot"):
    sys.modules.pop(m, None)
spec = importlib.util.spec_from_file_location("cand_bot", os.path.join(bot_dir, "bot.py"))
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
import contextlib, io
pts = []; dur = []
for s in range(start, start + n):
    with contextlib.redirect_stderr(io.StringIO()):
        r = fs.FaithfulMatch(mod.Bot, s).run()
    pts.append(r["points"]); dur.append(r["duration_s"])
print(f"{os.path.basename(bot_dir)} n={n} mean_pts={statistics.mean(pts):.3f} sd={statistics.pstdev(pts):.2f} surv={statistics.mean(dur):.1f}s win={sum(p==15 for p in pts)}")
