"""仿真 24h 请求量: 旧(5请求/轮, 60-180min) vs 新(3请求/轮, 15-360min)。"""
from datetime import datetime, timedelta


def simulate(req_per_refresh, base, near, far_h, runs=("08:00", "10:00", "16:00", "20:00", "22:00"),
             events=("06:23", "18:06")):
    t = datetime(2026, 10, 9, 0, 0); end = t + timedelta(days=1)
    ev = [datetime(2026, 10, 9, 6, 23), datetime(2026, 10, 9, 18, 6),
          datetime(2026, 10, 10, 6, 23), datetime(2026, 10, 10, 18, 6)]
    run_ts = [datetime.strptime(f"2026-10-{9+d} {h}", "%Y-%m-%d %H:%M") for d in (0, 1) for h in runs]
    n = 0; unchanged = 0
    while t < end:
        n += 1
        unchanged = 0 if any(abs((t - r).total_seconds()) < 1800 for r in run_ts) else unchanged + 1
        nxt = min([e for e in ev if e >= t], default=None)
        h = (nxt - t).total_seconds() / 3600 if nxt else 99
        want = (min(near, base) if h <= 1 else
                max(near, base // 2) if h <= 3 else
                base if h <= 12 else max(base, int(far_h * 60)))
        if unchanged and h > 6:
            want = min(want * min(unchanged + 1, 3), 480)
        t += timedelta(minutes=max(5, want))
    return n, n * req_per_refresh


old_n, old_r = simulate(5, 60, 15, 3)
new_n, new_r = simulate(3, 90, 15, 6)
print(f"旧: 5 请求/轮, 60/180min 档 -> {old_n:>3} 轮/天 -> {old_r:>4} 请求/天/地点")
print(f"新: 3 请求/轮, 90/360min 档 -> {new_n:>3} 轮/天 -> {new_r:>4} 请求/天/地点")
print(f"降幅: {100 - 100*new_r//old_r}%   (免费额度 10000/天, 10 个地点也够: {new_r*10}/天)")
