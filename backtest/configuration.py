"""YAML settings. Explicit arguments override configuration, never vice versa."""
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path

import yaml

UNSET = object()
CONFIG_DIR = Path(__file__).with_name("config")


def _merge(base, patch):
    result = deepcopy(base)
    for key, value in patch.items():
        if key not in result:
            raise ValueError(f"unknown configuration key: {key}")
        if isinstance(result[key], dict) and not isinstance(value, Mapping):
            raise ValueError(f"configuration section {key} must be a mapping")
        if isinstance(result[key], dict) and isinstance(value, Mapping):
            if key == "instrument_types":
                result[key].update(deepcopy(dict(value)))
            else:
                result[key] = _merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


class Config:
    """Defensive configuration snapshot with validated section/key names."""
    def __init__(self, values):
        self._values = deepcopy(values)

    def section(self, name):
        return deepcopy(self._values[name])

    def to_dict(self):
        return deepcopy(self._values)

    def override(self, **sections):
        return Config(_merge(self._values, sections))


def load_config(path=None, *, overrides=None):
    with (CONFIG_DIR / "default.yaml").open(encoding="utf-8") as stream:
        base = yaml.safe_load(stream)
    if isinstance(path, Config):
        values = path.to_dict()
    elif isinstance(path, Mapping):
        values = _merge(base, path)
    elif path is not None:
        with Path(path).open(encoding="utf-8") as stream:
            patch = yaml.safe_load(stream)
        if not isinstance(patch, Mapping):
            raise ValueError("YAML configuration must be a mapping")
        values = _merge(base, patch)
    else:
        values = base
    return Config(_merge(values, overrides or {}))


def resolve_config(config=None, *, legacy=False):
    return load_config(CONFIG_DIR / "legacy.yaml" if config is None and legacy else config)


def configured(argument, settings, name):
    """UNSET means absent; explicit None is a real override (e.g. no stop)."""
    return settings[name] if argument is UNSET else argument
