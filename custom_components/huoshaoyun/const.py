"""常量定义 —— 火烧云/朝霞-晚霞预报集成"""
from __future__ import annotations

DOMAIN = "huoshaoyun"
NAME = "火烧云 / 朝霞晚霞预报"
VERSION = "1.3.0"
PLATFORMS = ["sensor"]

# 配置项
CONF_NAME = "name"
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
CONF_MODELS = "models"
CONF_AMOUNT_SRC = "amount_src"
CONF_INTERVAL = "update_interval"
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
DEFAULT_INTERVAL = 180       # 刷新间隔(分钟)。恒定节拍, 不做临近提速
FAILURE_RETRY_MIN = 10       # 普通取数失败后的重试间隔(分钟)
# Open-Meteo 免费额度(按 IP): <10000次/天, 5000/小时, 600/分钟
# 配额用尽时必须长退避, 否则"失败→立刻重试"会自激成死循环、永远无法恢复
QUOTA_BACKOFF_MIN = 30       # 配额用尽的首次退避(分钟), 此后逐次翻倍
QUOTA_BACKOFF_CAP_MIN = 720  # 退避上限(分钟)
FORECAST_DAYS = 2
