import os
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class DefaultConfig:
    interval_seconds: int
    timeout_seconds: float
    user_agent: str


@dataclass
class RuleConfig:
    kind: str
    value: str


@dataclass
class ModuleConfig:
    slug: str
    url: str
    interval_seconds: int
    timeout_seconds: float
    user_agent: str
    rule: RuleConfig
    service_filter: List[str]
    enabled: bool


@dataclass
class AppConfig:
    modules: List[ModuleConfig]
    defaults: DefaultConfig
    log_level: str
    notifications: "NotificationConfig"


@dataclass
class TelegramConfig:
    enabled: bool
    bot_token: Optional[str]
    chat_ids: List[str]
    api_url: str
    timestamp_format: str
    timestamp_zone: str


@dataclass
class WebhookConfig:
    enabled: bool
    url: Optional[str]
    token: Optional[str]
    header_name: str


@dataclass
class NotificationConfig:
    telegram: TelegramConfig
    webhook: WebhookConfig
    repeat_minutes: int
    error_threshold: int = 3


def load_app_config() -> AppConfig:
    defaults = DefaultConfig(
        interval_seconds=_get_int("SERVICE_MONITOR_DEFAULT_INTERVAL_SECONDS", 60),
        timeout_seconds=_get_float("SERVICE_MONITOR_DEFAULT_TIMEOUT_SECONDS", 10.0),
        user_agent=os.getenv("SERVICE_MONITOR_DEFAULT_USER_AGENT", "service-checker/1.0"),
    )

    module_slugs = _get_module_slugs()
    modules = [_load_module_config(slug, defaults) for slug in module_slugs]

    log_level = os.getenv("SERVICE_MONITOR_LOG_LEVEL", "INFO").upper()

    notifications = _load_notification_config()

    return AppConfig(
        modules=modules,
        defaults=defaults,
        log_level=log_level,
        notifications=notifications,
    )


def _get_module_slugs() -> List[str]:
    raw = os.getenv("SERVICE_MONITOR_MODULES")
    if raw is None or not raw.strip():
        return ["steam"]
    return [slug.strip().lower() for slug in raw.split(",") if slug.strip()]


def _load_module_config(slug: str, defaults: DefaultConfig) -> ModuleConfig:
    prefix = slug.upper()
    url = os.getenv(f"{prefix}_URL", _default_url(slug))
    interval_seconds = _get_int(f"{prefix}_INTERVAL_SECONDS", defaults.interval_seconds)
    timeout_seconds = _get_float(f"{prefix}_TIMEOUT_SECONDS", defaults.timeout_seconds)
    user_agent = os.getenv(f"{prefix}_USER_AGENT", defaults.user_agent)
    rule_kind = os.getenv(f"{prefix}_RULE_KIND", "status").lower()
    rule_value = os.getenv(f"{prefix}_RULE_VALUE", "major,minor")
    service_filter = _get_service_filter(f"{prefix}_SERVICE_FILTER")
    enabled = _get_bool(f"{prefix}_ENABLED", True)

    return ModuleConfig(
        slug=slug,
        url=url,
        interval_seconds=interval_seconds,
        timeout_seconds=timeout_seconds,
        user_agent=user_agent,
        rule=RuleConfig(kind=rule_kind, value=rule_value),
        service_filter=service_filter,
        enabled=enabled,
    )


def _default_url(slug: str) -> str:
    if slug.lower() == "steam":
        return "https://steamstat.us/"
    if slug.lower() == "openai":
        return "https://status.openai.com/api/v2/summary.json"
    if slug.lower() == "claude":
        return "https://status.claude.com/api/v2/summary.json"
    if slug.lower() == "rockstar":
        return "https://support.rockstargames.com/servicestatus"
    if slug.lower() == "oci":
        return "https://ocistatus.oraclecloud.com/api/v2/incident-summary.rss"
    if slug.lower() == "aws":
        return "https://health.aws.amazon.com/public/currentevents"
    if slug.lower() == "gcp":
        return "https://status.cloud.google.com/incidents.json"
    if slug.lower() == "github":
        return "https://www.githubstatus.com/api/v2/summary.json"
    if slug.lower() == "bitbucket":
        return "https://bitbucket.status.atlassian.com/api/v2/summary.json"
    return f"https://{slug}.example.com/"


def _get_int(env_name: str, default: int) -> int:
    raw = os.getenv(env_name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_float(env_name: str, default: float) -> float:
    raw = os.getenv(env_name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _get_service_filter(env_name: str) -> List[str]:
    raw = os.getenv(env_name, "")
    if not raw.strip():
        return []
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def _get_bool(env_name: str, default: bool) -> bool:
    raw = os.getenv(env_name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _load_notification_config() -> NotificationConfig:
    chat_ids_env = os.getenv("TELEGRAM_CHAT_IDS")
    if chat_ids_env:
        chat_ids = [cid.strip() for cid in chat_ids_env.split(",") if cid.strip()]
    else:
        single_chat = os.getenv("TELEGRAM_CHAT_ID")
        chat_ids = [single_chat] if single_chat else []

    telegram = TelegramConfig(
        enabled=_get_bool("TELEGRAM_ENABLED", False),
        bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        chat_ids=chat_ids,
        api_url=os.getenv("TELEGRAM_API_URL", "https://api.telegram.org"),
        timestamp_format=os.getenv(
            "TELEGRAM_TIMESTAMP_FORMAT", "%Y-%m-%d %H:%M:%S %Z"
        ),
        timestamp_zone=os.getenv("TELEGRAM_TIMESTAMP_ZONE", "UTC"),
    )
    webhook = WebhookConfig(
        enabled=_get_bool("WEBHOOK_ENABLED", False),
        url=os.getenv("WEBHOOK_URL"),
        token=os.getenv("WEBHOOK_TOKEN"),
        header_name=os.getenv("WEBHOOK_HEADER_NAME", "Authorization"),
    )
    repeat_minutes = _get_int("NOTIFICATION_REPEAT_MINUTES", 10)
    if repeat_minutes < 1:
        repeat_minutes = 1
    error_threshold = _get_int("NOTIFICATION_ERROR_THRESHOLD", 3)
    if error_threshold < 1:
        error_threshold = 1
    return NotificationConfig(
        telegram=telegram,
        webhook=webhook,
        repeat_minutes=repeat_minutes,
        error_threshold=error_threshold,
    )
