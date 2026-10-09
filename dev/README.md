# dev/ —— 本地自检工具（不参与集成运行）

| 脚本 | 需要联网 | 说明 |
|---|---|---|
| `test_offline.py` | ❌ | **纯逻辑自检**：请求分档节拍、未变退避、失败语义、事件刻印。用 `_teststub/` 的最小 homeassistant 桩，秒级跑完 |
| `sim_requests.py` | ❌ | 仿真 24h 请求量（旧固定节拍 vs 新分档+退避） |
| `test_local.py` | ✅ | 算法 + 取数链路自检（需自备坐标） |
| `test_integration.py` | ✅ | coordinator + 实体装配自检（需自备坐标） |
| `dump_outputs.py` | ✅ | 导出某地点当前对外输出的完整清单（JSON） |

坐标必须自己传，仓库里**不含任何地点**：

```bash
cd dev
python3 test_offline.py
python3 test_local.py       --lat 30.0 --lon 120.0 --name 我的地点
python3 test_integration.py --lat 30.0 --lon 120.0 --name 我的地点
python3 dump_outputs.py     --lat 30.0 --lon 120.0 --name 我的地点
```

`_teststub/homeassistant/` 是给离线自检用的**最小桩**，只实现了用到的几个类/函数，
不是 Home Assistant 源码，运行时不会被加载（HA 里用的是真的 `homeassistant`）。

> 注意：`test_local.py` / `test_integration.py` 各跑一次是 5~10 个请求。
> Open-Meteo 免费额度有**小时级**上限，反复跑会撞 `429`（集成本身有退避与重试）。
