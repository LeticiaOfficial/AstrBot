"""Process-wide outgoing message policy, independent of chat configuration."""

from functools import wraps
from typing import Any

_root_config: dict | None = None


def configure_outgoing_gate(config: dict) -> None:
    """Bind the live administrator configuration.

    Args:
        config: Root configuration, never a per-chat override.
    """
    global _root_config
    _root_config = config


def outgoing_messages_enabled() -> bool:
    """Read the fail-closed switch on every send.

    Returns:
        Whether the administrator explicitly enabled outgoing messages.
    """
    return bool(
        _root_config is not None
        and _root_config.get("platform_settings", {}).get("outgoing_messages_enabled")
        is True
    )


def guard_outgoing(method):
    """Guard an asynchronous send without consuming its streaming input.

    Args:
        method: Platform or event sending method.

    Returns:
        Wrapped method that skips sends when the switch is disabled.
    """

    @wraps(method)
    async def guarded(*args, **kwargs):
        if not outgoing_messages_enabled():
            return None
        return await method(*args, **kwargs)

    return guarded


def guard_send_overrides(cls: type, names: tuple[str, ...]) -> None:
    """Guard concrete overrides before any adapter-specific network activity.

    Args:
        cls: Newly defined event or platform subclass.
        names: Outgoing entry points to protect.
    """
    for name in names:
        method = cls.__dict__.get(name)
        if isinstance(method, staticmethod):
            setattr(cls, name, staticmethod(guard_outgoing(method.__func__)))
        elif isinstance(method, classmethod):
            setattr(cls, name, classmethod(guard_outgoing(method.__func__)))
        elif method is not None:
            setattr(cls, name, guard_outgoing(method))


async def guarded_onebot_action(action_call, action: str, **params) -> Any:
    """Block protocol-level sends used directly by background plugins.

    Args:
        action_call: Original OneBot action callable.
        action: OneBot action, including its asynchronous variant.
        **params: Protocol parameters.

    Returns:
        Original protocol response for permitted actions.

    Raises:
        PermissionError: Sending is disabled by the administrator.
    """
    normalized = action.removesuffix("_async").removesuffix("_rate_limited")
    if (
        normalized.startswith("send_")
        or normalized
        in {
            "upload_group_file",
            "upload_private_file",
            "set_group_notice",
            "_send_group_notice",
        }
    ) and not outgoing_messages_enabled():
        raise PermissionError("Outgoing messages are disabled by the administrator")
    return await action_call(action, **params)
