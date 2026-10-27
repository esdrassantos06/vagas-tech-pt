"""Job sources registry."""

from __future__ import annotations

from ptjobs.sources import itjobs, linkedin, net_empregos
from ptjobs.sources.base import RawJob

__all__ = ["RawJob", "itjobs", "linkedin", "net_empregos"]
