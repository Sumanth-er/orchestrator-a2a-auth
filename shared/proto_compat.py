"""Compatibility shim for older a2a-sdk versions on modern protobuf.

a2a-sdk releases earlier than 1.0.0 use `FieldDescriptor.label`, an attribute
that was removed in protobuf 5.x+. The result is an
`AttributeError: 'FieldDescriptor' object has no attribute 'label'` raised
deep inside the SDK's request validator.

This module restores `.label` as a derived property so old a2a-sdk versions
keep working without an upgrade.

It also forces protobuf to use the pure-Python implementation, because the
default UPB (C-extension) `FieldDescriptor` type can't be monkey-patched.

Usage: import this module as the FIRST import in any process that imports
a2a-sdk. The agent and orchestrator main.py files do this.
"""
from __future__ import annotations

import os

# Must be set before `google.protobuf` is loaded for the first time.
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

from google.protobuf.descriptor import FieldDescriptor  # noqa: E402

if not hasattr(FieldDescriptor, "label"):
    def _label(self: FieldDescriptor) -> int:
        # proto3 has no LABEL_REQUIRED, so just distinguish repeated vs. singular.
        return (
            FieldDescriptor.LABEL_REPEATED
            if self.is_repeated
            else FieldDescriptor.LABEL_OPTIONAL
        )

    FieldDescriptor.label = property(_label)  # type: ignore[attr-defined]
