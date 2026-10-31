"""Display strings for values Live doesn't give one for, measured on Live 11.

AbletonOSC returns a device parameter's display string ("2.50 kHz") but not the mixer's:
volume, pan and sends come back as raw floats. The tables here are filled from the write
probe (scripts/probe_writes.py, fixtures/writes/probe_live11.json).
"""

# Raw fader value -> dB, as Live's mixer shows it. Filled from the probe.
VOLUME_DB = []


def volume_display(value, send=False):
    """Live's text for a raw volume or send level, like "-6.0 dB", or None if unknown."""
    if not VOLUME_DB:
        return None
    return None


def parameter_display(class_name, param, value):
    """A device parameter's display string for `value`, for FakeLive.

    Uses the probe's measured curve for stock devices when there is one (the nearest
    measured point), else a plain number. `param` is a track-state parameter dict.
    """
    curve = _CURVES.get((class_name, param.get("name")))
    if curve:
        return min(curve, key=lambda point: abs(point[0] - value))[1]
    if param.get("is_quantized"):
        return str(int(round(value)))
    return f"{value:.2f}"


# (class_name, parameter name) -> [(raw, display)]. Filled from the probe.
_CURVES = {}
