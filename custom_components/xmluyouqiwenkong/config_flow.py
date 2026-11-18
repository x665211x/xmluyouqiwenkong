"""配置向导：在界面上填写路由器 IP、端口、Telnet 账号密码、刷新间隔、告警阈值。"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

try:  # HA 2024.4 起才有 ConfigFlowResult
    from homeassistant.config_entries import ConfigFlowResult
except ImportError:  # 更老的 HA 回退到 FlowResult，避免向导整体加载失败
    from homeassistant.data_entry_flow import FlowResult as ConfigFlowResult

from .const import (
    CONF_HOST,
    CONF_PROTOCOL,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_TEMP_THRESHOLD,
    CONF_TIMEOUT,
    CONF_USERNAME,
    DEFAULT_PASSWORD,
    DEFAULT_PORT,
    DEFAULT_PROTOCOL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TEMP_THRESHOLD,
    DEFAULT_SSH_PORT,
    DEFAULT_TIMEOUT,
    DEFAULT_USERNAME,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MAX_TEMP_THRESHOLD,
    MAX_TIMEOUT,
    MIN_SCAN_INTERVAL,
    MIN_TEMP_THRESHOLD,
    MIN_TIMEOUT,
    PROTOCOL_SSH,
    PROTOCOL_TELNET,
)
from .coordinator import XiaomiRouterCoordinator
from .telnet_client import TelnetAuthError, TelnetError

_LOGGER = logging.getLogger(__name__)

_CONN_FIELDS = (CONF_PROTOCOL, CONF_HOST, CONF_PORT, CONF_USERNAME, CONF_PASSWORD)
_OPTION_FIELDS = (CONF_SCAN_INTERVAL, CONF_TIMEOUT, CONF_TEMP_THRESHOLD)

DEFAULT_HOST = "192.168.1.1"


def _schema(defaults: dict[str, Any], *, with_threshold: bool = False) -> vol.Schema:
    """连接参数 + 刷新参数；选项向导额外带一个告警阈值。"""
    protocol = defaults.get(CONF_PROTOCOL) or DEFAULT_PROTOCOL
    fields: dict[Any, Any] = {
        vol.Optional(CONF_PROTOCOL, default=protocol): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(
                        value=PROTOCOL_TELNET, label="Telnet（默认 23 端口）"
                    ),
                    selector.SelectOptionDict(
                        value=PROTOCOL_SSH, label="SSH（默认 22 端口，需要 paramiko）"
                    ),
                ],
                mode=selector.SelectSelectorMode.LIST,
            )
        ),
        vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, DEFAULT_HOST)): str,
        vol.Optional(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=65535)
        ),
        vol.Optional(
            CONF_USERNAME, default=defaults.get(CONF_USERNAME, DEFAULT_USERNAME)
        ): str,
        vol.Optional(
            CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, DEFAULT_PASSWORD)
        ): str,
        vol.Optional(
            CONF_SCAN_INTERVAL,
            default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        ): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)
        ),
        vol.Optional(
            CONF_TIMEOUT, default=defaults.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
        ): vol.All(vol.Coerce(int), vol.Range(min=MIN_TIMEOUT, max=MAX_TIMEOUT)),
    }
    if with_threshold:
        fields[
            vol.Optional(
                CONF_TEMP_THRESHOLD,
                default=defaults.get(CONF_TEMP_THRESHOLD, DEFAULT_TEMP_THRESHOLD),
            )
        ] = vol.All(
            vol.Coerce(float),
            vol.Range(min=MIN_TEMP_THRESHOLD, max=MAX_TEMP_THRESHOLD),
        )
    return vol.Schema(fields)


def _normalize(address: Any, port: Any) -> tuple[str, int | None]:
    """把用户随手输入的地址整理成 (host, port)。

    支持 192.168.31.1、192.168.31.1:23、telnet://192.168.31.1、http://192.168.31.1/ 等写法。
    """
    host = str(address or "").strip()
    for prefix in ("telnet://", "http://", "https://"):
        if host.lower().startswith(prefix):
            host = host[len(prefix) :]
    host = host.split("/")[0]
    port_value: int | None = None
    if port not in (None, ""):
        try:
            port_value = int(port)
        except (TypeError, ValueError):
            port_value = None
    if host.count(":") == 1:
        head, _, tail = host.partition(":")
        if tail.strip().isdigit():
            host = head
            port_value = int(tail.strip())
    return host.strip(), port_value


def _split(user_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """连接参数进 data，刷新/阈值参数进 options。"""
    conn = {key: user_input[key] for key in _CONN_FIELDS if key in user_input}
    opts = {key: user_input[key] for key in _OPTION_FIELDS if key in user_input}
    return conn, opts


def _normalize_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """统一整理表单数据（协议、地址、端口、用户名、密码）。"""
    protocol = str(user_input.get(CONF_PROTOCOL) or DEFAULT_PROTOCOL).lower()
    if protocol not in (PROTOCOL_TELNET, PROTOCOL_SSH):
        protocol = DEFAULT_PROTOCOL
    user_input[CONF_PROTOCOL] = protocol

    host, port = _normalize(user_input.get(CONF_HOST, ""), user_input.get(CONF_PORT))
    if port is None:
        # 没填端口（地址里也没有 :端口）→ 按协议给默认值
        port = DEFAULT_SSH_PORT if protocol == PROTOCOL_SSH else DEFAULT_PORT
    elif protocol == PROTOCOL_SSH and port == DEFAULT_PORT:
        # 选了 SSH 却还是 Telnet 的 23，视为"没改"，自动换成 22
        port = DEFAULT_SSH_PORT
    elif protocol == PROTOCOL_TELNET and port == DEFAULT_SSH_PORT:
        port = DEFAULT_PORT

    user_input[CONF_HOST] = host
    user_input[CONF_PORT] = port
    user_input[CONF_USERNAME] = (
        str(user_input.get(CONF_USERNAME) or "").strip() or DEFAULT_USERNAME
    )
    user_input[CONF_PASSWORD] = str(user_input.get(CONF_PASSWORD) or "")
    return user_input


async def _async_probe(hass, merged: dict[str, Any]) -> dict[str, Any]:
    """临时建一个协调器验证连通性并返回一次解析结果。"""
    probe_entry = type(
        "ProbeEntry", (), {"data": merged, "options": {}, "entry_id": "probe"}
    )()
    coordinator = XiaomiRouterCoordinator(hass, probe_entry)  # type: ignore[arg-type]
    return await coordinator.async_probe()


class XiaomiRouterConfigFlow(ConfigFlow, domain=DOMAIN):
    """首次添加路由器时的向导。"""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            user_input = _normalize_input(user_input)
            if not user_input[CONF_HOST]:
                errors["base"] = "cannot_connect"
                return self.async_show_form(
                    step_id="user", data_schema=_schema(user_input), errors=errors
                )

            await self.async_set_unique_id(user_input[CONF_HOST])
            self._abort_if_unique_id_configured()

            try:
                values = await _async_probe(self.hass, user_input)
            except TelnetAuthError as err:
                _LOGGER.debug("认证失败：%s", err)
                errors["base"] = "invalid_auth"
            except TelnetError as err:
                _LOGGER.debug("连接失败：%s", err)
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - 兜底，避免向导崩溃
                _LOGGER.exception("验证路由器连接时出现未知错误")
                errors["base"] = "unknown"
            else:
                if values.get("uptime") is None and values.get("cpu_temperature") is None:
                    errors["base"] = "cannot_connect"
                else:
                    conn, opts = _split(user_input)
                    opts.setdefault(CONF_TEMP_THRESHOLD, DEFAULT_TEMP_THRESHOLD)
                    return self.async_create_entry(
                        title=f"小米路由器 {conn[CONF_HOST]}", data=conn, options=opts
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """账号或密码变了以后重新输入。"""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry: ConfigEntry | None = getattr(self, "_reauth_entry", None)
        if entry is None:
            return self.async_abort(reason="reauth_failed")

        if user_input is not None:
            merged = {**entry.data, **entry.options}
            merged[CONF_USERNAME] = (
                str(user_input.get(CONF_USERNAME) or "").strip() or DEFAULT_USERNAME
            )
            merged[CONF_PASSWORD] = str(user_input.get(CONF_PASSWORD) or "")
            try:
                await _async_probe(self.hass, merged)
            except TelnetAuthError:
                errors["base"] = "invalid_auth"
            except TelnetError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("重新验证时出现未知错误")
                errors["base"] = "unknown"
            else:
                new_data = {
                    **entry.data,
                    CONF_USERNAME: merged[CONF_USERNAME],
                    CONF_PASSWORD: merged[CONF_PASSWORD],
                }
                self.hass.config_entries.async_update_entry(entry, data=new_data)
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_USERNAME, default=entry.data.get(CONF_USERNAME, "root")
                    ): str,
                    vol.Optional(
                        CONF_PASSWORD, default=entry.data.get(CONF_PASSWORD, "")
                    ): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return XiaomiRouterOptionsFlow(config_entry)


class XiaomiRouterOptionsFlow(OptionsFlow):
    """修改 IP / 端口 / 账号 / 刷新间隔 / 告警阈值。"""

    def __init__(self, config_entry: ConfigEntry | None = None) -> None:
        self._entry = config_entry

    @property
    def entry(self) -> ConfigEntry:
        entry = getattr(self, "config_entry", None)
        return entry if entry is not None else self._entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self.entry
        current: dict[str, Any] = {**entry.data, **entry.options}

        if user_input is not None:
            user_input = _normalize_input(user_input)
            try:
                await _async_probe(self.hass, user_input)
            except TelnetAuthError:
                errors["base"] = "invalid_auth"
            except TelnetError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("修改配置时验证失败")
                errors["base"] = "unknown"
            else:
                conn, opts = _split(user_input)
                if CONF_TEMP_THRESHOLD not in opts:
                    opts[CONF_TEMP_THRESHOLD] = current.get(
                        CONF_TEMP_THRESHOLD, DEFAULT_TEMP_THRESHOLD
                    )
                self.hass.config_entries.async_update_entry(entry, data=conn)
                return self.async_create_entry(title="", data=opts)

        return self.async_show_form(
            step_id="init",
            data_schema=_schema(user_input or current, with_threshold=True),
            errors=errors,
        )
