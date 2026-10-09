"""仿真 24h 请求量: 恒定节拍, 3 个请求/轮。"""


def daily(interval_min, req=3):
    return 24 * 60 // interval_min, (24 * 60 // interval_min) * req


print(f"{'间隔':>8}  {'轮/天':>6}  {'请求/天/地点':>12}")
for iv in (180, 240, 360, 720):
    n, r = daily(iv)
    print(f"{iv:>6} 分  {n:>6}  {r:>12}")
print()
print("对比: v1.0.x 5请求/轮 + 临近提速(15min) 约 200 请求/天")
print("      v1.1.0 3请求/轮 + 分档(15/45/90/360) 约  42 请求/天")
print("现行   3请求/轮 + 恒定 180 分钟         -> 24 请求/天/地点")
print("免费额度 10000/天: 10 个地点也只用 240/天")
