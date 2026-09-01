"""Support for Huawei LTE routers."""

from collections import defaultdict
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import functools
import logging
from typing import Any, cast
from xml.parsers.expat import ExpatError
import asyncio

from huawei_lte_api.Client import Client
from huawei_lte_api.Connection import Connection
from huawei_lte_api.exceptions import (
    LoginErrorInvalidCredentialsException,
    ResponseErrorException,
    ResponseErrorLoginRequiredException,
    ResponseErrorNotSupportedException,
)
from requests.exceptions import ConnectionError as RequestsConnectionError, Timeout
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_HW_VERSION,
    ATTR_MODEL,
    ATTR_SW_VERSION,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_RECIPIENT,
    CONF_URL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    discovery,
    entity_registry as er,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.typing import ConfigType

from .const import (
    ADMIN_SERVICES,
    ALL_KEYS,
    CONF_AUTO_DELETE_SMS,
    CONF_MANUFACTURER,
    CONF_UNAUTHENTICATED_MODE,
    CONF_UNSUPPORTED_KEYS,
    CONF_UPNP_UDN,
    CONNECTION_TIMEOUT,
    DEFAULT_AUTO_DELETE_SMS,
    DEFAULT_DEVICE_NAME,
    DEFAULT_MANUFACTURER,
    DEFAULT_NOTIFY_SERVICE_NAME,
    DOMAIN,
    HUAWEI_LTE_CONFIG,
    EVENT_SMS_RECEIVED,
    KEY_DEVICE_BASIC_INFORMATION,
    KEY_DEVICE_INFORMATION,
    KEY_DEVICE_SIGNAL,
    KEY_DIALUP_MOBILE_DATASWITCH,
    KEY_LAN_HOST_INFO,
    KEY_MONITORING_CHECK_NOTIFICATIONS,
    KEY_MONITORING_MONTH_STATISTICS,
    KEY_MONITORING_STATUS,
    KEY_MONITORING_TRAFFIC_STATISTICS,
    KEY_NET_CURRENT_PLMN,
    KEY_NET_NET_MODE,
    KEY_SMS_SMS_COUNT,
    KEY_SMS_LAST_RECEIVED,
    KEY_WLAN_HOST_LIST,
    KEY_WLAN_WIFI_FEATURE_SWITCH,
    KEY_WLAN_WIFI_GUEST_NETWORK_SWITCH,
    SERVICE_CLEAR_ALL_SMS,
    SERVICE_CLEAR_SMS_DRAFTS,
    SERVICE_CLEAR_SMS_INBOX,
    SERVICE_CLEAR_SMS_REPORTS,
    SERVICE_CLEAR_SMS_SENT,
    SERVICE_DELETE_SMS,
    SERVICE_RESET_UNSUPPORTED_ENDPOINTS,
    SERVICE_RESEND_SMS_DRAFTS,
    SERVICE_RESUME_INTEGRATION,
    SERVICE_SUSPEND_INTEGRATION,
    UPDATE_SIGNAL,
)
import xml.etree.ElementTree as ET
from .utils import get_device_macs, non_verifying_requests_session

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=30)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_SCHEMA = vol.Schema({vol.Optional(CONF_URL): cv.url})

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.DEVICE_TRACKER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


def _clean_sms_content(raw_content: str) -> str:
    """Decode and clean SMS content (UCS-2 hex, double-encoded UTF-8 mojibake)."""
    if not raw_content:
        return ""
    s = raw_content.strip()
    if len(s) >= 4 and len(s) % 4 == 0 and all(c in "0123456789abcdefABCDEF" for c in s):
        try:
            decoded = bytes.fromhex(s).decode("utf-16-be")
            if decoded and all(ord(c) >= 32 or c in "\n\r\t" for c in decoded):
                return decoded
        except Exception:
            pass

    text = raw_content
    for _ in range(2):
        if any(c in text for c in ("Ã", "Â", "â", "ã", "ä")):
            try:
                candidate = text.encode("latin-1").decode("utf-8")
                text = candidate
            except (UnicodeEncodeError, UnicodeDecodeError):
                break
        else:
            break
    return text


@dataclass
class Router:
    """Class for router state."""

    hass: HomeAssistant
    config_entry: "HuaweiLteConfigEntry"
    connection: Connection
    url: str

    data: dict[str, Any] = field(default_factory=dict, init=False)
    # Values are lists rather than sets, because the same item may be used by more than
    # one thing, such as MonthDuration for CurrentMonth{Download,Upload}.
    subscriptions: dict[str, list[str]] = field(init=False)
    inflight_gets: set[str] = field(default_factory=set, init=False)
    client: Client = field(init=False)
    suspended: bool = field(default=False, init=False)
    api_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    seen_sms_indices: set[str] = field(default_factory=set, init=False)
    _initial_sms_scanned: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        """Set up internal state on init."""
        self.client = Client(self.connection)
        # NEW: skip endpoints that were previously auto-detected as
        # unsupported by this specific router, so we do not even attempt
        # (and warn about) them again after a restart.
        unsupported = set(self.config_entry.options.get(CONF_UNSUPPORTED_KEYS, []))
        self.subscriptions = defaultdict(
            list,
            (
                (x, ["initial_scan"])
                for x in ALL_KEYS
                if x not in unsupported
            ),
        )
        if unsupported:
            _LOGGER.debug(
                "Skipping previously detected unsupported endpoints: %s",
                sorted(unsupported),
            )

    def refresh_session(self) -> bool:
        """Force refresh CSRF token and session cookies without reloading integration."""
        try:
            _LOGGER.debug("Refreshing Huawei LTE session tokens")
            url = f"{self.url.rstrip('/')}/api/webserver/SesTokInfo"
            r = self.connection.requests_session.get(url, timeout=CONNECTION_TIMEOUT)
            root = ET.fromstring(r.text)
            tok = root.findtext("TokInfo")
            ses = root.findtext("SesInfo")
            if tok:
                if hasattr(self.connection, "_token"):
                    self.connection._token = tok
                if hasattr(self.connection, "token"):
                    self.connection.token = tok
            if ses:
                cookie_name = "SessionID"
                cookie_val = ses.replace("SessionID=", "").strip()
                self.connection.requests_session.cookies.set(cookie_name, cookie_val)

            if not self.config_entry.options.get(CONF_UNAUTHENTICATED_MODE):
                username = self.config_entry.data.get(CONF_USERNAME, "")
                password = self.config_entry.data.get(CONF_PASSWORD, "")
                if username or password:
                    self.client.user.login(username, password)
            return True
        except Exception as ex:
            _LOGGER.warning("Could not refresh Huawei LTE session: %s", ex)
            return False

    def _api_get_sms_list(self, page: int = 1, count: int = 20, box_type: int = 1) -> list[dict[str, str]]:
        """Get SMS list using PascalCase XML tags required by Huawei HiLink."""
        url = f"{self.url.rstrip('/')}/api/sms/sms-list"
        tok_url = f"{self.url.rstrip('/')}/api/webserver/SesTokInfo"
        try:
            r_tok = self.connection.requests_session.get(tok_url, timeout=CONNECTION_TIMEOUT)
            root_tok = ET.fromstring(r_tok.content)
            tok = root_tok.findtext("TokInfo") or ""
            ses = root_tok.findtext("SesInfo") or ""

            headers = {
                "__RequestVerificationToken": tok,
                "Cookie": ses,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
            payload = f"""<?xml version="1.0" encoding="UTF-8"?><request><PageIndex>{page}</PageIndex><ReadCount>{count}</ReadCount><BoxType>{box_type}</BoxType><SortType>0</SortType><Ascending>0</Ascending><UnreadPreferred>0</UnreadPreferred></request>"""
            r = self.connection.requests_session.post(url, data=payload, headers=headers, timeout=CONNECTION_TIMEOUT)

            root = ET.fromstring(r.content)
            msgs = []
            for m in root.findall(".//Message"):
                content = _clean_sms_content(m.findtext("Content") or "")
                msgs.append({
                    "Index": m.findtext("Index") or "",
                    "Phone": m.findtext("Phone") or "",
                    "Content": content,
                    "Date": m.findtext("Date") or "",
                    "SmsType": m.findtext("SmsType") or "",
                })
            return msgs
        except Exception as ex:
            _LOGGER.warning("Error fetching SMS list: %s", ex)
            return []

    def _api_delete_sms(self, index: int | str) -> bool:
        """Delete an SMS by index using PascalCase XML tags."""
        url = f"{self.url.rstrip('/')}/api/sms/delete-sms"
        tok_url = f"{self.url.rstrip('/')}/api/webserver/SesTokInfo"
        try:
            r_tok = self.connection.requests_session.get(tok_url, timeout=CONNECTION_TIMEOUT)
            root_tok = ET.fromstring(r_tok.text)
            tok = root_tok.findtext("TokInfo") or ""
            ses = root_tok.findtext("SesInfo") or ""
            headers = {
                "__RequestVerificationToken": tok,
                "Cookie": ses,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
            payload = f"""<?xml version="1.0" encoding="UTF-8"?><request><Index>{index}</Index></request>"""
            r = self.connection.requests_session.post(url, data=payload, headers=headers, timeout=CONNECTION_TIMEOUT)
            return "<response>OK</response>" in r.text
        except Exception as ex:
            _LOGGER.warning("Error deleting SMS index %s: %s", index, ex)
            return False

    def _api_send_sms(self, targets: list[str] | str, message: str) -> bool:
        """Send an SMS using direct HiLink XML POST and fresh session token."""
        url = f"{self.url.rstrip('/')}/api/sms/send-sms"
        tok_url = f"{self.url.rstrip('/')}/api/webserver/SesTokInfo"

        if isinstance(targets, str):
            targets = [t.strip() for t in targets.split(",") if t.strip()]

        if not targets or not message:
            _LOGGER.warning("Cannot send SMS: empty targets (%s) or message (%s)", targets, message)
            return False

        try:
            r_tok = self.connection.requests_session.get(tok_url, timeout=CONNECTION_TIMEOUT)
            root_tok = ET.fromstring(r_tok.text)
            tok = root_tok.findtext("TokInfo") or ""
            ses = root_tok.findtext("SesInfo") or ""

            headers = {
                "__RequestVerificationToken": tok,
                "Cookie": ses,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }

            req = ET.Element("request")
            ET.SubElement(req, "Index").text = "-1"
            phones_elem = ET.SubElement(req, "Phones")
            for phone in targets:
                ET.SubElement(phones_elem, "Phone").text = str(phone)
            ET.SubElement(req, "Sca").text = ""
            ET.SubElement(req, "Content").text = str(message)
            ET.SubElement(req, "Length").text = str(len(str(message)))
            ET.SubElement(req, "Reserved").text = "1"
            ET.SubElement(req, "Date").text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            payload = f'<?xml version="1.0" encoding="UTF-8"?>{ET.tostring(req, encoding="utf-8").decode("utf-8")}'

            r = self.connection.requests_session.post(
                url, data=payload.encode("utf-8"), headers=headers, timeout=CONNECTION_TIMEOUT
            )

            if "<response>OK</response>" in r.text or "<response>1</response>" in r.text:
                _LOGGER.info("SMS envoyé avec succès à %s", targets)
                return True

            try:
                root_resp = ET.fromstring(r.text)
                err_code = root_resp.findtext("code") or ""
                err_msg = root_resp.findtext("message") or ""
                _LOGGER.error(
                    "Erreur lors de l'envoi du SMS à %s - code %s (%s): %s",
                    targets,
                    err_code,
                    err_msg,
                    r.text,
                )
            except Exception:
                _LOGGER.error("Erreur lors de l'envoi du SMS à %s, réponse: %s", targets, r.text)

            return False
        except Exception as ex:
            _LOGGER.error("Exception lors de l'envoi du SMS à %s : %s", targets, ex)
            return False

    def _check_incoming_sms(self) -> None:
        """Check for incoming SMS, dispatch HA events and update last received sensor."""
        try:
            messages = self._api_get_sms_list(page=1, count=20, box_type=1)
            new_incoming = []
            for msg in messages:
                idx = msg.get("Index", "")
                sms_type = msg.get("SmsType", "")
                content = msg.get("Content", "")
                phone = msg.get("Phone", "")
                date_str = msg.get("Date", "")

                # Auto-delete delivery reports (type 7) to keep storage free
                if sms_type == "7" and idx:
                    self._api_delete_sms(idx)
                    continue

                if not content and sms_type not in ("1", "2"):
                    continue

                if idx and idx not in self.seen_sms_indices:
                    self.seen_sms_indices.add(idx)
                    if self._initial_sms_scanned:
                        new_incoming.append({
                            "phone": phone,
                            "message": content,
                            "date": date_str,
                            "index": idx,
                        })

            auto_delete = self.config_entry.options.get(
                CONF_AUTO_DELETE_SMS, DEFAULT_AUTO_DELETE_SMS
            )
            for sms_data in reversed(new_incoming):
                _LOGGER.info("Incoming SMS from %s: %s", sms_data["phone"], sms_data["message"])
                self.hass.bus.fire(EVENT_SMS_RECEIVED, sms_data)
                self.data[KEY_SMS_LAST_RECEIVED] = sms_data
                if auto_delete and sms_data.get("index"):
                    self._api_delete_sms(sms_data["index"])

            if not self._initial_sms_scanned and messages:
                for msg in messages:
                    if msg.get("Content"):
                        self.data[KEY_SMS_LAST_RECEIVED] = {
                            "phone": msg.get("Phone", ""),
                            "message": msg.get("Content", ""),
                            "date": msg.get("Date", ""),
                            "index": msg.get("Index", ""),
                        }
                        break
                self._initial_sms_scanned = True
        except Exception as ex:
            _LOGGER.warning("Error checking incoming SMS: %s", ex, exc_info=True)

    @property
    def device_name(self) -> str:
        """Get router device name."""
        for key, item in (
            (KEY_DEVICE_BASIC_INFORMATION, "devicename"),
            (KEY_DEVICE_INFORMATION, "DeviceName"),
        ):
            with suppress(KeyError, TypeError):
                return cast(str, self.data[key][item])
        return DEFAULT_DEVICE_NAME

    @property
    def device_identifiers(self) -> set[tuple[str, str]]:
        """Get router identifiers for device registry."""
        assert self.config_entry.unique_id is not None
        return {(DOMAIN, self.config_entry.unique_id)}

    @property
    def device_connections(self) -> set[tuple[str, str]]:
        """Get router connections for device registry."""
        connections = {
            (dr.CONNECTION_NETWORK_MAC, x) for x in self.config_entry.data[CONF_MAC]
        }
        if udn := self.config_entry.data.get(CONF_UPNP_UDN):
            connections.add((dr.CONNECTION_UPNP, udn))
        return connections

    def _mark_unsupported(self, key: str) -> None:
        """Exclude key from future updates and persist that decision.

        Persisting means we will not even try this endpoint again after a
        Home Assistant restart, avoiding the repeated warning log entries.
        """
        self.subscriptions.pop(key, None)

        current = set(self.config_entry.options.get(CONF_UNSUPPORTED_KEYS, []))
        if key in current:
            return
        current.add(key)
        new_options = {
            **self.config_entry.options,
            CONF_UNSUPPORTED_KEYS: sorted(current),
        }

        # config_entries.async_update_entry must run on the event loop; update()
        # (and thus this method) runs in an executor thread, so schedule it.
        # async_update_entry only accepts `entry` positionally -- `data` and
        # `options` are keyword-only -- and hass.add_job does not support
        # passing kwargs directly, so bind them with functools.partial.
        self.hass.add_job(
            functools.partial(
                self.hass.config_entries.async_update_entry,
                self.config_entry,
                options=new_options,
            )
        )

    def _get_data(self, key: str, func: Callable[[], Any]) -> None:
        if not self.subscriptions.get(key):
            return
        if key in self.inflight_gets:
            _LOGGER.debug("Skipping already in-flight get for %s", key)
            return
        self.inflight_gets.add(key)
        _LOGGER.debug("Getting %s for subscribers %s", key, self.subscriptions[key])
        try:
            self.data[key] = func()
        except ResponseErrorLoginRequiredException:
            if not self.config_entry.options.get(CONF_UNAUTHENTICATED_MODE):
                _LOGGER.debug("Trying to authorize again")
                if self.client.user.login(
                    self.config_entry.data.get(CONF_USERNAME, ""),
                    self.config_entry.data.get(CONF_PASSWORD, ""),
                ):
                    _LOGGER.debug(
                        "success, %s will be updated by a future periodic run",
                        key,
                    )
                else:
                    _LOGGER.debug("failed")
                return
            _LOGGER.warning(
                "%s requires authorization, excluding from future updates", key
            )
            self._mark_unsupported(key)
        except (RequestsConnectionError, Timeout):
            # Transient network hiccup (e.g. router reset the TCP connection
            # mid-request). Not a support issue, just skip this cycle and
            # retry on the next periodic update.
            _LOGGER.debug(
                "%s: transient connection error, will retry next update", key,
                exc_info=True,
            )
        except (ResponseErrorException, ExpatError) as exc:
            # Check for token/session expiration on HiLink
            if isinstance(exc, ResponseErrorException) and getattr(exc, "code", None) in (-1, 100003, 100005, 100006, 125001, 125002):
                _LOGGER.debug("%s: session or token expired (code %s), refreshing session", key, getattr(exc, "code", None))
                self.refresh_session()
                return

            # Take ResponseErrorNotSupportedException, ExpatError, and generic
            # ResponseErrorException with a few select codes to mean the endpoint is
            # not supported.
            if not isinstance(
                exc, (ResponseErrorNotSupportedException, ExpatError)
            ) and exc.code not in (-1, 100006):
                raise
            _LOGGER.warning(
                "%s apparently not supported by device, excluding from future updates"
                " (this will be remembered, so it will not be retried on restart;"
                " use the 'Reset unsupported endpoints' action to probe again)",
                key,
            )
            self._mark_unsupported(key)
        finally:
            self.inflight_gets.discard(key)
            _LOGGER.debug("%s=%s", key, self.data.get(key))

    def update(self) -> None:
        """Update router data."""

        if self.suspended:
            _LOGGER.debug("Integration suspended, not updating data")
            return

        self._get_data(KEY_DEVICE_INFORMATION, self.client.device.information)
        if self.data.get(KEY_DEVICE_INFORMATION):
            # Full information includes everything in basic
            self.subscriptions.pop(KEY_DEVICE_BASIC_INFORMATION, None)
        self._get_data(
            KEY_DEVICE_BASIC_INFORMATION, self.client.device.basic_information
        )
        self._get_data(KEY_DEVICE_SIGNAL, self.client.device.signal)
        self._get_data(
            KEY_DIALUP_MOBILE_DATASWITCH, self.client.dial_up.mobile_dataswitch
        )
        self._get_data(
            KEY_MONITORING_MONTH_STATISTICS, self.client.monitoring.month_statistics
        )
        self._get_data(
            KEY_MONITORING_CHECK_NOTIFICATIONS,
            self.client.monitoring.check_notifications,
        )
        self._get_data(KEY_MONITORING_STATUS, self.client.monitoring.status)
        self._get_data(
            KEY_MONITORING_TRAFFIC_STATISTICS, self.client.monitoring.traffic_statistics
        )
        self._get_data(KEY_NET_CURRENT_PLMN, self.client.net.current_plmn)
        self._get_data(KEY_NET_NET_MODE, self.client.net.net_mode)
        self._get_data(KEY_SMS_SMS_COUNT, self.client.sms.sms_count)
        self._check_incoming_sms()
        self._get_data(KEY_LAN_HOST_INFO, self.client.lan.host_info)
        if self.data.get(KEY_LAN_HOST_INFO):
            # LAN host info includes everything in WLAN host list
            self.subscriptions.pop(KEY_WLAN_HOST_LIST, None)
        self._get_data(KEY_WLAN_HOST_LIST, self.client.wlan.host_list)
        self._get_data(
            KEY_WLAN_WIFI_FEATURE_SWITCH, self.client.wlan.wifi_feature_switch
        )
        self._get_data(
            KEY_WLAN_WIFI_GUEST_NETWORK_SWITCH,
            lambda: next(
                (
                    ssid
                    for ssid in self.client.wlan.multi_basic_settings()
                    .get("Ssids", {})
                    .get("Ssid", [])
                    if isinstance(ssid, dict) and ssid.get("wifiisguestnetwork") == "1"
                ),
                {},
            ),
        )

        dispatcher_send(self.hass, UPDATE_SIGNAL, self.config_entry.unique_id)

    def logout(self) -> None:
        """Log out router session."""
        try:
            self.client.user.logout()
        except (
            ResponseErrorLoginRequiredException,
            ResponseErrorNotSupportedException,
        ):
            pass
        except ResponseErrorException as ex:
            # Le code 100006 (Unknown/session invalide) est fréquent sur la clé E3372
            # après une coupure réseau. La session étant déjà invalidée côté routeur,
            # ce n'est pas une erreur bloquante à faire remonter.
            if str(getattr(ex, "code", "")) == "100006":
                _LOGGER.debug("Logout ignoré (session déjà invalide - code 100006)")
            else:
                _LOGGER.warning("Logout error", exc_info=True)
        except Exception:
            _LOGGER.warning("Logout error", exc_info=True)

    def cleanup(self, *_: Any) -> None:
        """Clean up resources."""
        self.subscriptions.clear()
        try:
            self.logout()
        except Exception:
            pass
        
        try:
            self.connection.requests_session.close()
        except Exception:
            pass


type HuaweiLteConfigEntry = ConfigEntry[Router]


async def async_setup_entry(hass: HomeAssistant, entry: HuaweiLteConfigEntry) -> bool:
    """Set up Huawei LTE component from config entry."""
    url = entry.data[CONF_URL]

    def _connect() -> Connection:
        """Set up a connection."""
        kwargs: dict[str, Any] = {
            "timeout": CONNECTION_TIMEOUT,
        }
        if url.startswith("https://") and not entry.data.get(CONF_VERIFY_SSL):
            kwargs["requests_session"] = non_verifying_requests_session(url)
        if entry.options.get(CONF_UNAUTHENTICATED_MODE):
            _LOGGER.debug("Connecting in unauthenticated mode, reduced feature set")
            connection = Connection(url, **kwargs)
        else:
            _LOGGER.debug("Connecting in authenticated mode, full feature set")
            username = entry.data.get(CONF_USERNAME) or ""
            password = entry.data.get(CONF_PASSWORD) or ""
            connection = Connection(url, username=username, password=password, **kwargs)
        return connection

    try:
        connection = await hass.async_add_executor_job(_connect)
    except LoginErrorInvalidCredentialsException as ex:
        raise ConfigEntryAuthFailed from ex
    except Timeout as ex:
        raise ConfigEntryNotReady from ex

    # Set up router
    router = Router(hass, entry, connection, url)

    # Do initial data update
    async with router.api_lock:
        await hass.async_add_executor_job(router.update)

    # Check that we found required information
    router_info = router.data.get(KEY_DEVICE_INFORMATION)
    if not entry.unique_id:
        # Transitional from < 2021.8: update None config entry and entity unique ids
        if router_info and (serial_number := router_info.get("SerialNumber")):
            hass.config_entries.async_update_entry(entry, unique_id=serial_number)
            ent_reg = er.async_get(hass)
            for entity_entry in er.async_entries_for_config_entry(
                ent_reg, entry.entry_id
            ):
                if not entity_entry.unique_id.startswith("None-"):
                    continue
                new_unique_id = entity_entry.unique_id.removeprefix("None-")
                new_unique_id = f"{serial_number}-{new_unique_id}"
                ent_reg.async_update_entity(
                    entity_entry.entity_id, new_unique_id=new_unique_id
                )
        else:
            await hass.async_add_executor_job(router.cleanup)
            msg = (
                "Could not resolve serial number to use as unique id for router at %s"
                ", setup failed"
            )
            if not entry.data.get(CONF_PASSWORD):
                msg += (
                    ". Try setting up credentials for the router for one startup, "
                    "unauthenticated mode can be enabled after that in integration "
                    "settings"
                )
            _LOGGER.error(msg, url)
            return False

    # Store reference to router
    entry.runtime_data = router

    # Clear all subscriptions, enabled entities will push back theirs
    router.subscriptions.clear()

    # Update device MAC addresses on record. These can change due to toggling between
    # authenticated and unauthenticated modes, or likely also when enabling/disabling
    # SSIDs in the router config.
    try:
        wlan_settings = await hass.async_add_executor_job(
            router.client.wlan.multi_basic_settings
        )
    except Exception:  # noqa: BLE001
        # Assume not supported, or authentication required but in unauthenticated mode
        wlan_settings = {}
    macs = get_device_macs(router_info or {}, wlan_settings)
    # Be careful not to overwrite a previous, more complete set with a partial one
    if macs and (not entry.data[CONF_MAC] or (router_info and wlan_settings)):
        new_data = dict(entry.data)
        new_data[CONF_MAC] = macs
        hass.config_entries.async_update_entry(entry, data=new_data)

    # Set up device registry
    if router.device_identifiers or router.device_connections:
        device_info = DeviceInfo(
            configuration_url=router.url,
            connections=router.device_connections,
            identifiers=router.device_identifiers,
            manufacturer=entry.data.get(CONF_MANUFACTURER, DEFAULT_MANUFACTURER),
            name=router.device_name,
        )
        hw_version = None
        sw_version = None
        if router_info:
            hw_version = router_info.get("HardwareVersion")
            sw_version = router_info.get("SoftwareVersion")
            if router_info.get("DeviceName"):
                device_info[ATTR_MODEL] = router_info["DeviceName"]
        if not sw_version and router.data.get(KEY_DEVICE_BASIC_INFORMATION):
            sw_version = router.data[KEY_DEVICE_BASIC_INFORMATION].get(
                "SoftwareVersion"
            )
        if hw_version:
            device_info[ATTR_HW_VERSION] = hw_version
        if sw_version:
            device_info[ATTR_SW_VERSION] = sw_version
        device_registry = dr.async_get(hass)
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            **device_info,
        )

    # Forward config entry setup to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Notify doesn't support config entry setup yet, load with discovery for now
    await discovery.async_load_platform(
        hass,
        Platform.NOTIFY,
        DOMAIN,
        {
            ATTR_CONFIG_ENTRY_ID: entry.entry_id,
            CONF_NAME: entry.options.get(CONF_NAME, DEFAULT_NOTIFY_SERVICE_NAME),
            CONF_RECIPIENT: entry.options.get(CONF_RECIPIENT),
        },
        hass.data[HUAWEI_LTE_CONFIG],
    )

    async def _update_router(*_: Any) -> None:
        """Update router data with a lock to prevent token desync."""
        async with router.api_lock:
            await hass.async_add_executor_job(router.update)

    entry.async_on_unload(
        async_track_time_interval(hass, _update_router, SCAN_INTERVAL)
    )

    # Clean up at end
    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, router.cleanup)
    )

    return True


async def async_unload_entry(
    hass: HomeAssistant, config_entry: HuaweiLteConfigEntry
) -> bool:
    """Unload config entry."""

    # Forward config entry unload to platforms
    await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)

    # Invoke router cleanup
    await hass.async_add_executor_job(config_entry.runtime_data.cleanup)

    return True


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Huawei LTE component."""

    hass.data[HUAWEI_LTE_CONFIG] = config

    async def async_service_handler(service: ServiceCall) -> None:
        """Apply a service.

        We key this using the router URL instead of its unique id / serial number,
        because the latter is not available anywhere in the UI.
        """
        routers = [
            entry.runtime_data
            for entry in hass.config_entries.async_loaded_entries(DOMAIN)
        ]
        if url := service.data.get(CONF_URL):
            router = next((router for router in routers if router.url == url), None)
        elif not routers:
            _LOGGER.error("%s: no routers configured", service.service)
            return
        elif len(routers) == 1:
            router = routers[0]
        else:
            _LOGGER.error(
                "%s: more than one router configured, must specify one of URLs %s",
                service.service,
                sorted(router.url for router in routers),
            )
            return
        if not router:
            _LOGGER.error("%s: router %s unavailable", service.service, url)
            return

        if service.service == SERVICE_RESUME_INTEGRATION:
            # Login will be handled automatically on demand
            router.suspended = False
            _LOGGER.debug("%s: %s", service.service, "done")
        elif service.service == SERVICE_SUSPEND_INTEGRATION:
            await hass.async_add_executor_job(router.logout)
            router.suspended = True
            _LOGGER.debug("%s: %s", service.service, "done")
        elif service.service == SERVICE_RESET_UNSUPPORTED_ENDPOINTS:
            new_options = {**router.config_entry.options, CONF_UNSUPPORTED_KEYS: []}
            hass.config_entries.async_update_entry(
                router.config_entry, options=new_options
            )
            hass.config_entries.async_schedule_reload(router.config_entry.entry_id)
            _LOGGER.debug("%s: %s", service.service, "done, reloading")
        elif service.service == SERVICE_CLEAR_SMS_REPORTS:
            async with router.api_lock:
                try:
                    messages = await hass.async_add_executor_job(
                        router._api_get_sms_list, 1, 50, 1
                    )
                    for msg in messages:
                        if msg.get("SmsType") == "7" and msg.get("Index"):
                            await hass.async_add_executor_job(
                                router._api_delete_sms, msg["Index"]
                            )
                    _LOGGER.info("Accusés de réception SMS nettoyés avec succès")
                except Exception as ex:
                    _LOGGER.warning("Erreur lors du nettoyage des accusés SMS: %s", ex)
        elif service.service == SERVICE_DELETE_SMS:
            idx = service.data.get("index")
            if idx is not None:
                async with router.api_lock:
                    try:
                        res = await hass.async_add_executor_job(router._api_delete_sms, idx)
                        if res:
                            _LOGGER.info("SMS index %s supprimé avec succès", idx)
                        else:
                            _LOGGER.warning("Échec suppression SMS index %s", idx)
                    except Exception as ex:
                        _LOGGER.warning("Erreur suppression SMS index %s: %s", idx, ex)
        elif service.service == SERVICE_CLEAR_SMS_INBOX:
            async with router.api_lock:
                try:
                    messages = await hass.async_add_executor_job(
                        router._api_get_sms_list, 1, 50, 1
                    )
                    count = 0
                    for msg in messages:
                        if msg.get("Index"):
                            if await hass.async_add_executor_job(router._api_delete_sms, msg["Index"]):
                                count += 1
                    _LOGGER.info("Boîte de réception SMS vidée (%s/%s messages supprimés)", count, len(messages))
                except Exception as ex:
                    _LOGGER.warning("Erreur vidage boîte SMS: %s", ex)
        elif service.service == SERVICE_CLEAR_SMS_DRAFTS:
            async with router.api_lock:
                try:
                    drafts = await hass.async_add_executor_job(
                        router._api_get_sms_list, 1, 50, 3
                    )
                    count = 0
                    for draft in drafts:
                        if draft.get("Index"):
                            if await hass.async_add_executor_job(router._api_delete_sms, draft["Index"]):
                                count += 1
                    _LOGGER.info("Brouillons SMS vidés (%s/%s messages supprimés)", count, len(drafts))
                except Exception as ex:
                    _LOGGER.warning("Erreur vidage brouillons SMS: %s", ex)
        elif service.service == SERVICE_CLEAR_SMS_SENT:
            async with router.api_lock:
                try:
                    sent_msgs = await hass.async_add_executor_job(
                        router._api_get_sms_list, 1, 50, 2
                    )
                    count = 0
                    for msg in sent_msgs:
                        if msg.get("Index"):
                            if await hass.async_add_executor_job(router._api_delete_sms, msg["Index"]):
                                count += 1
                    _LOGGER.info("SMS envoyés vidés (%s/%s messages supprimés)", count, len(sent_msgs))
                except Exception as ex:
                    _LOGGER.warning("Erreur vidage SMS envoyés: %s", ex)
        elif service.service == SERVICE_CLEAR_ALL_SMS:
            async with router.api_lock:
                try:
                    total = 0
                    for box_t in (1, 2, 3, 4):
                        msgs = await hass.async_add_executor_job(
                            router._api_get_sms_list, 1, 50, box_t
                        )
                        for msg in msgs:
                            if msg.get("Index"):
                                if await hass.async_add_executor_job(router._api_delete_sms, msg["Index"]):
                                    total += 1
                    _LOGGER.info("Mémoire SMS totalement purgée (%s messages supprimés)", total)
                except Exception as ex:
                    _LOGGER.warning("Erreur purge totale SMS: %s", ex)
        elif service.service == SERVICE_RESEND_SMS_DRAFTS:
            async with router.api_lock:
                try:
                    drafts = await hass.async_add_executor_job(
                        router._api_get_sms_list, 1, 50, 3
                    )
                    success_cnt = 0
                    for draft in drafts:
                        phone = draft.get("Phone")
                        content = draft.get("Content")
                        idx = draft.get("Index")
                        if phone and content:
                            sent = await hass.async_add_executor_job(
                                router._api_send_sms, [phone], content
                            )
                            if sent:
                                success_cnt += 1
                                if idx:
                                    await hass.async_add_executor_job(
                                        router._api_delete_sms, idx
                                    )
                    _LOGGER.info("Renvoi des brouillons SMS : %s/%s renvoyés avec succès", success_cnt, len(drafts))
                except Exception as ex:
                    _LOGGER.warning("Erreur lors du renvoi des brouillons SMS: %s", ex)
        else:
            _LOGGER.error("%s: unsupported service", service.service)

    service_schemas = {
        SERVICE_DELETE_SMS: vol.Schema(
            {
                vol.Optional(CONF_URL): cv.url,
                vol.Required("index"): vol.Any(cv.positive_int, cv.string),
            }
        ),
    }

    for service in ADMIN_SERVICES:
        async_register_admin_service(
            hass,
            DOMAIN,
            service,
            async_service_handler,
            schema=service_schemas.get(service, SERVICE_SCHEMA),
        )

    return True


async def async_migrate_entry(
    hass: HomeAssistant, config_entry: HuaweiLteConfigEntry
) -> bool:
    """Migrate config entry to new version."""
    if config_entry.version == 1:
        options = dict(config_entry.options)
        recipient = options.get(CONF_RECIPIENT)
        if isinstance(recipient, str):
            options[CONF_RECIPIENT] = [x.strip() for x in recipient.split(",")]
        hass.config_entries.async_update_entry(config_entry, options=options, version=2)
        _LOGGER.debug("Migrated config entry to version %d", config_entry.version)
    if config_entry.version == 2:
        data = dict(config_entry.data)
        data[CONF_MAC] = []
        hass.config_entries.async_update_entry(config_entry, data=data, version=3)
        _LOGGER.debug("Migrated config entry to version %d", config_entry.version)
    # There can be no longer needed *_from_yaml data and options things left behind
    # from pre-2022.4ish; they can be removed while at it when/if we eventually bump and
    # migrate to version > 3 for some other reason.
    return True