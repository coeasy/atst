# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Static capability and Provider contract facts.

This package holds the *declarations* the execution kernel consults but never
mutates: the capability catalog and its call validation, the Provider
``channel -> adapter`` binding tables, and the Provider-isolation contract,
guards and consistency audits. It performs no provider selection and has no
fallback behaviour; runtime execution lives in :mod:`tstdx.runtime`.

Submodules are imported by their full path (``tstdx.catalog.capability``) so
the dependency direction toward :mod:`tstdx.runtime` stays one-way.
"""
