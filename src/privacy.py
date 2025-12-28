from __future__ import annotations

from enum import StrEnum

PD_POLICY_VERSION = "2025-12-28"


class ConsentRole(StrEnum):
    MASTER = "master"
    CLIENT = "client"
