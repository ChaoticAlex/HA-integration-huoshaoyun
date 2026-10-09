"""常量定义 —— 火烧云/朝霞-晚霞预报集成"""
from __future__ import annotations

DOMAIN = "huoshaoyun"
NAME = "火烧云 / 朝霞晚霞预报"
VERSION = "1.0.0"
PLATFORMS = ["sensor"]

# 配置项
CONF_NAME = "name"
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
CONF_MODELS = "models"
CONF_AMOUNT_SRC = "amount_src"
CONF_INTERVAL = "update_interval"
CONF_NEAR_INTERVAL = "near_interval"
CONF_TIMEZONE = "timezone"

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
DEFAULT_INTERVAL = 60        # 常规刷新(分钟)
DEFAULT_NEAR_INTERVAL = 15   # 临近事件时的刷新(分钟)
NEAR_WINDOW_HOURS = 3        # 距事件 ≤3h 视为"临近"
FAILURE_RETRY_MIN = 5        # 取数失败后, 下次重试提前到几分钟(默认档位可能长达 3h)
FORECAST_DAYS = 2
