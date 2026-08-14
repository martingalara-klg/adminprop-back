"""AuditService transversal -- ver `service.py` (issue #10)."""

from adminprop.shared.audit.service import audit, record_access_denied

__all__ = ["audit", "record_access_denied"]
