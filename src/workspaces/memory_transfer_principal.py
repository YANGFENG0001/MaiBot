"""Stable non-secret principal identity for transfer ownership."""

from hashlib import sha256


def principal_id(context) -> str:
    values = (
        str(getattr(context, "person_id", "")),
        str(getattr(context, "workspace_id", "")),
        str(getattr(context, "active_bot_profile_id", "")),
        str(getattr(context, "permission_group_id", "")),
        str(getattr(context, "security_domain", "normal")),
    )
    return sha256(("memory-transfer:principal:v1\0" + "\0".join(values)).encode("utf-8")).hexdigest()


def namespaced_idempotency_key(context, client_key: str) -> str:
    value = str(client_key or "").strip()
    if not value:
        return ""
    return sha256(
        (
            "memory-transfer:v1\0"
            + principal_id(context)
            + "\0"
            + value
        ).encode("utf-8")
    ).hexdigest()
