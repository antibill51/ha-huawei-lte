"""Support for Huawei LTE router notifications."""

import asyncio
import logging
from typing import Any, override

from homeassistant.components.notify import ATTR_TARGET, BaseNotificationService
from homeassistant.const import ATTR_CONFIG_ENTRY_ID, CONF_RECIPIENT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import HuaweiLteConfigEntry, Router

_LOGGER = logging.getLogger(__name__)


async def async_get_service(
    hass: HomeAssistant,
    config: ConfigType,
    discovery_info: DiscoveryInfoType | None = None,
) -> "HuaweiLteSmsNotificationService" | None:
    """Get the notification service."""
    if discovery_info is None:
        return None

    entry: HuaweiLteConfigEntry | None = hass.config_entries.async_get_entry(
        discovery_info[ATTR_CONFIG_ENTRY_ID]
    )
    if entry is None or not hasattr(entry, "runtime_data"):
        return None

    router = entry.runtime_data
    default_targets = discovery_info.get(CONF_RECIPIENT) or []

    return HuaweiLteSmsNotificationService(router, default_targets)


class HuaweiLteSmsNotificationService(BaseNotificationService):
    """Huawei LTE router SMS notification service."""

    def __init__(self, router: Router, default_targets: list[str]) -> None:
        """Initialize."""
        self.router = router
        self.default_targets = default_targets

    @override
    async def async_send_message(self, message: str = "", **kwargs: Any) -> None:
        """Send message to target numbers with async lock and direct HiLink API."""
        targets = kwargs.get(ATTR_TARGET, self.default_targets)
        if not targets or not message:
            return

        if self.router.suspended:
            _LOGGER.debug(
                "Integration suspended, not sending notification to %s", targets
            )
            return

        async with self.router.api_lock:
            # 1er essai direct avec token frais
            success = await self.router.hass.async_add_executor_job(
                self.router._api_send_sms, targets, message
            )
            if success:
                return

            _LOGGER.warning(
                "Échec temporaire envoi SMS à %s. Nouvel essai dans 1 seconde...",
                targets,
            )
            await asyncio.sleep(1)

            # 2ème essai
            success = await self.router.hass.async_add_executor_job(
                self.router._api_send_sms, targets, message
            )
            if not success:
                _LOGGER.error("Erreur définitive lors de l'envoi du SMS à %s", targets)
