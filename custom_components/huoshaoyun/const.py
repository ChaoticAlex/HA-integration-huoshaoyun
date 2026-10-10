"""常量定义 —— 火烧云/朝霞-晚霞预报集成"""
from __future__ import annotations

DOMAIN = "huoshaoyun"
NAME = "火烧云 / 朝霞晚霞预报"
VERSION = "1.6.0"
PLATFORMS = ["sensor"]

# 配置项
CONF_NAME = "name"
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
CONF_MODELS = "models"
CONF_AMOUNT_SRC = "amount_src"
CONF_SLOT_MARGIN = "slot_margin"
CONF_TIMEZONE = "timezone"
CONF_API_KEY = "api_key"

# 模式选择
MODEL_CHOICES = {"both": "GFS + ECMWF 双模式(推荐)",
                 "gfs": "仅 NCEP-GFS",
                 "ecmwf": "仅 ECMWF-IFS"}
MODELS_MAP = {"both": ("gfs_global", "ecmwf_ifs025"),
              "gfs": ("gfs_global",),
              "ecmwf": ("ecmwf_ifs025",)}

# 云量来源
SRC_CHOICES = {"mean": "诊断云量与廓线反演的均值(推荐)",
               "rh": "湿度廓线反演云量",
               "diag": "模式诊断云量(LCDC/MCDC/HCDC)"}
SRC_LABEL = {"mean": "均值", "rh": "廓线反演", "diag": "诊断云量"}

# 默认值
# 上游起报数据的"可用时刻"(北京时间, 时:分) —— 见 docs/SCHEDULE.md
#   GFS    00/06/12/18Z, 约 +5.5h 可用 -> 01:30 / 07:30 / 13:30 / 19:30
#   ECMWF  00/06/12/18Z, 约 +8h   可用 -> 04:00 / 10:00 / 16:00 / 22:00
# 刷新只在"可用时刻 + 余量"之后触发, 从而每次都拿到最新起报; 其余时间不发请求。
AVAILABILITY_LOCAL = (
    ("01:30", "GFS 12Z"), ("04:00", "ECMWF 12Z"), ("07:30", "GFS 18Z"), ("10:00", "ECMWF 18Z"),
    ("13:30", "GFS 00Z"), ("16:00", "ECMWF 00Z"), ("19:30", "GFS 06Z"), ("22:00", "ECMWF 06Z"),
)
DEFAULT_SLOT_MARGIN = 15     # 起报延迟余量(分钟)。官方建议"可用后再等 10 分钟"是最小值,
                             # 但官方同页也说"小延迟很常见", 故默认 15
MISS_RETRY_MIN = 20          # 到了刷新点却拿不到新起报(入库延迟)时的重试间隔
MISS_RETRY_MAX = 3           # 每个刷新点最多补试几次, 之后等下一个刷新点
FAILURE_RETRY_MIN = 10       # 普通取数失败后的重试间隔(分钟)
# Open-Meteo 免费额度(按 IP): <10000次/天, 5000/小时, 600/分钟
# 配额用尽时必须长退避, 否则"失败→立刻重试"会自激成死循环、永远无法恢复
QUOTA_BACKOFF_MIN = 30       # 配额用尽的首次退避(分钟), 此后逐次翻倍
QUOTA_BACKOFF_CAP_MIN = 720  # 退避上限(分钟)
FORECAST_DAYS = 2
