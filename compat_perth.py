"""Compatibilidad para resemble-perth 1.0.1 con Python moderno.

Perth todavía importa ``pkg_resources.resource_filename`` aunque solo lo usa
para localizar su carpeta ``pretrained``. Entornos creados por uv no tienen
por qué incluir setuptools/pkg_resources. Este shim aporta únicamente esa
operación mediante importlib y evita instalar una dependencia obsoleta.
"""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
import types


def ensure_pkg_resources_compat() -> None:
    try:
        import pkg_resources  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    module = types.ModuleType("pkg_resources")

    def resource_filename(package_or_requirement: object, resource_name: str) -> str:
        package_name = str(package_or_requirement)
        package = importlib.import_module(package_name)
        package_file = getattr(package, "__file__", None)
        if not package_file:
            raise FileNotFoundError(f"No se pudo localizar el paquete {package_name}")
        return str((Path(package_file).resolve().parent / resource_name).resolve())

    module.resource_filename = resource_filename  # type: ignore[attr-defined]
    sys.modules["pkg_resources"] = module
