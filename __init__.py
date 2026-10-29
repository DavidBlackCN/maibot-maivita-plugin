"""MaiVita. AGPL-3.0; see docs/SOURCES.md."""
__all__ = ["MaiVitaPlugin", "create_plugin"]
__version__ = "0.5.4"


def __getattr__(name):
    if name in {"MaiVitaPlugin", "AutonomousPlanningPluginV4", "create_plugin"}:
        from . import plugin
        return getattr(plugin, name)
    raise AttributeError(name)
