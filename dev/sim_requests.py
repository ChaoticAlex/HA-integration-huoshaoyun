"""按当前节拍逻辑仿真 24h 请求量(旧固定节拍 vs 新分档+退避)。"""
from datetime import datetime, timedelta

def simulate(base=60, near=15, runs=("08:00","10:00","16:00","20:00","22:00"),
             events=("06:23","18:06"), old=False):
    t = datetime(2026,10,9,0,0); end = t + timedelta(days=1)
    ev = [datetime(2026,10,9,6,23), datetime(2026,10,9,18,6),
          datetime(2026,10,10,6,23), datetime(2026,10,10,18,6)]
    run_ts = [datetime.strptime(f"2026-10-{9+d} {h}", "%Y-%m-%d %H:%M")
              for d in (0,1) for h in runs]
    n = 0; unchanged = 0; fp_seq = []
    while t < end:
        n += 1
        # 本次拉取时, 指纹是否变化(有新起报)
        changed = any(abs((t - r).total_seconds()) < 1800 for r in run_ts)
        unchanged = 0 if changed else unchanged + 1
        nxt = min([e for e in ev if e >= t], default=None)
        h = (nxt - t).total_seconds()/3600 if nxt else 99
        if old:
            want = near if h <= 3 else base
        else:
            want = (min(near, base) if h <= 1 else
                    max(near, base//2) if h <= 3 else
                    base if h <= 12 else max(base, 180))
            if unchanged and h > 6:
                want = min(want * min(unchanged+1, 3), 360)
        t += timedelta(minutes=max(5, want))
    return n

o = simulate(old=True); n = simulate(old=False)
print(f"旧固定节拍 : {o:>3} 次刷新/天 -> {o*5:>4} 请求/天/地点")
print(f"新分档+退避: {n:>3} 次刷新/天 -> {n*5:>4} 请求/天/地点   (降 {100-100*n//o}%)")
