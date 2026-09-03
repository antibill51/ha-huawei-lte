"""Huawei LTE constants."""

from homeassistant.helpers.typing import ConfigType
from homeassistant.util.hass_dict import HassKey

DOMAIN = "huawei_lte"

HUAWEI_LTE_CONFIG: HassKey[ConfigType] = HassKey(DOMAIN)

CONF_MANUFACTURER = "manufacturer"
CONF_TRACK_WIRED_CLIENTS = "track_wired_clients"
CONF_UNAUTHENTICATED_MODE = "unauthenticated_mode"
CONF_UPNP_UDN = "upnp_udn"

# NEW: persisted list of endpoint keys detected as unsupported by the
# connected router. Populated automatically the first time an endpoint
# fails with a "not supported" style error, so that subsequent restarts
# do not attempt (and warn about) those endpoints again.
CONF_UNSUPPORTED_KEYS = "unsupported_keys"

DEFAULT_DEVICE_NAME = "LTE"
DEFAULT_MANUFACTURER = "Huawei Technologies Co., Ltd."
DEFAULT_NOTIFY_SERVICE_NAME = DOMAIN
DEFAULT_TRACK_WIRED_CLIENTS = True
DEFAULT_UNAUTHENTICATED_MODE = False

UPDATE_SIGNAL = f"{DOMAIN}_update"

CONNECTION_TIMEOUT = 30

CONF_AUTO_DELETE_SMS = "auto_delete_sms"
DEFAULT_AUTO_DELETE_SMS = False

SERVICE_RESUME_INTEGRATION = "resume_integration"
SERVICE_SUSPEND_INTEGRATION = "suspend_integration"
# NEW: lets a user force re-probing of endpoints previously marked
# unsupported (e.g. after a firmware update re-enabled a feature).
SERVICE_RESET_UNSUPPORTED_ENDPOINTS = "reset_unsupported_endpoints"
SERVICE_CLEAR_SMS_REPORTS = "clear_sms_reports"
SERVICE_DELETE_SMS = "delete_sms"
SERVICE_CLEAR_SMS_INBOX = "clear_sms_inbox"
SERVICE_CLEAR_SMS_DRAFTS = "clear_sms_drafts"
SERVICE_CLEAR_SMS_SENT = "clear_sms_sent"
SERVICE_CLEAR_ALL_SMS = "clear_all_sms"
SERVICE_RESEND_SMS_DRAFTS = "resend_sms_drafts"

EVENT_SMS_RECEIVED = "huawei_lte_sms_received"

ADMIN_SERVICES = {
    SERVICE_RESUME_INTEGRATION,
    SERVICE_SUSPEND_INTEGRATION,
    SERVICE_RESET_UNSUPPORTED_ENDPOINTS,
    SERVICE_CLEAR_SMS_REPORTS,
    SERVICE_DELETE_SMS,
    SERVICE_CLEAR_SMS_INBOX,
    SERVICE_CLEAR_SMS_DRAFTS,
    SERVICE_CLEAR_SMS_SENT,
    SERVICE_CLEAR_ALL_SMS,
    SERVICE_RESEND_SMS_DRAFTS,
}

KEY_DEVICE_BASIC_INFORMATION = "device_basic_information"
KEY_DEVICE_INFORMATION = "device_information"
KEY_DEVICE_SIGNAL = "device_signal"
KEY_DIALUP_MOBILE_DATASWITCH = "dialup_mobile_dataswitch"
KEY_LAN_HOST_INFO = "lan_host_info"
KEY_MONITORING_CHECK_NOTIFICATIONS = "monitoring_check_notifications"
KEY_MONITORING_MONTH_STATISTICS = "monitoring_month_statistics"
KEY_MONITORING_STATUS = "monitoring_status"
KEY_MONITORING_TRAFFIC_STATISTICS = "monitoring_traffic_statistics"
KEY_NET_CURRENT_PLMN = "net_current_plmn"
KEY_NET_NET_MODE = "net_net_mode"
KEY_SMS_SMS_COUNT = "sms_sms_count"
KEY_SMS_LAST_RECEIVED = "sms_last_received"
KEY_WLAN_HOST_LIST = "wlan_host_list"
KEY_WLAN_WIFI_FEATURE_SWITCH = "wlan_wifi_feature_switch"
KEY_WLAN_WIFI_GUEST_NETWORK_SWITCH = "wlan_wifi_guest_network_switch"

BINARY_SENSOR_KEYS = {
    KEY_MONITORING_CHECK_NOTIFICATIONS,
    KEY_MONITORING_STATUS,
    KEY_WLAN_WIFI_FEATURE_SWITCH,
}

DEVICE_TRACKER_KEYS = {
    KEY_LAN_HOST_INFO,
    KEY_WLAN_HOST_LIST,
}

SENSOR_KEYS = {
    KEY_DEVICE_INFORMATION,
    KEY_DEVICE_SIGNAL,
    KEY_MONITORING_CHECK_NOTIFICATIONS,
    KEY_MONITORING_MONTH_STATISTICS,
    KEY_MONITORING_STATUS,
    KEY_MONITORING_TRAFFIC_STATISTICS,
    KEY_NET_CURRENT_PLMN,
    KEY_NET_NET_MODE,
    KEY_SMS_SMS_COUNT,
}

SWITCH_KEYS = {KEY_DIALUP_MOBILE_DATASWITCH, KEY_WLAN_WIFI_GUEST_NETWORK_SWITCH}

ALL_KEYS = (
    BINARY_SENSOR_KEYS
    | DEVICE_TRACKER_KEYS
    | SENSOR_KEYS
    | SWITCH_KEYS
    | {KEY_DEVICE_BASIC_INFORMATION}
)

BUTTON_KEY_CLEAR_TRAFFIC_STATISTICS = "clear_traffic_statistics"
BUTTON_KEY_RESTART = "restart"
