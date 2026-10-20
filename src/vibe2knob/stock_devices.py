"""Live's stock devices (Live 11/12, Suite), used to keep advice on stock gear.

Keys are the names Live shows in the browser. CLASS_NAMES maps a few common
internal class names (what AbletonOSC reports) back to those names; it is
best-effort, so matching falls back to the device's display name.
"""

from __future__ import annotations

AUDIO_EFFECTS = {
    "Align Delay", "Amp", "Audio Effect Rack", "Auto Filter", "Auto Pan",
    "Auto Pan-Tremolo", "Auto Shift", "Beat Repeat", "Cabinet", "Channel EQ",
    "Chorus-Ensemble", "Color Limiter", "Compressor", "Corpus", "Delay", "Drum Buss",
    "Dynamic Tube", "Echo", "EQ Eight", "EQ Three", "Erosion", "Filter Delay", "Gate",
    "Gated Delay", "Glue Compressor", "Grain Delay", "Hybrid Reverb", "Limiter",
    "Looper", "Multiband Dynamics", "Overdrive", "Pedal", "Phaser-Flanger",
    "Pitch Hack", "Re-Enveloper", "Redux", "Resonators", "Reverb", "Roar",
    "Saturator", "Shifter", "Spectral Blur", "Spectral Resonator", "Spectral Time",
    "Spectrum", "Tuner", "Utility", "Vinyl Distortion", "Vocoder",
}

INSTRUMENTS = {
    "Analog", "Collision", "Drift", "Drum Rack", "Drum Sampler", "Electric",
    "Impulse", "Instrument Rack", "Meld", "Operator", "Sampler", "Simpler",
    "Tension", "Wavetable",
}

MIDI_EFFECTS = {
    "Arpeggiator", "Chord", "MIDI Effect Rack", "Note Echo", "Note Length", "Pitch",
    "Random", "Scale", "Velocity",
}

STOCK_DEVICES = AUDIO_EFFECTS | INSTRUMENTS | MIDI_EFFECTS

CLASS_NAMES = {
    "AutoFilter": "Auto Filter",
    "AutoPan": "Auto Pan",
    "BeatRepeat": "Beat Repeat",
    "ChannelEq": "Channel EQ",
    "Chorus2": "Chorus-Ensemble",
    "Compressor2": "Compressor",
    "Delay": "Delay",
    "DrumBuss": "Drum Buss",
    "Echo": "Echo",
    "Eq8": "EQ Eight",
    "FilterEQ3": "EQ Three",
    "GlueCompressor": "Glue Compressor",
    "GrainDelay": "Grain Delay",
    "Hybrid": "Hybrid Reverb",
    "Limiter": "Limiter",
    "MultibandDynamics": "Multiband Dynamics",
    "OriginalSimpler": "Simpler",
    "PhaserNew": "Phaser-Flanger",
    "Redux2": "Redux",
    "Reverb": "Reverb",
    "Roar": "Roar",
    "Saturator": "Saturator",
    "StereoGain": "Utility",
    "Vinyl": "Vinyl Distortion",
    "InstrumentVector": "Wavetable",
    "Operator": "Operator",
    "UltraAnalog": "Analog",
    "Drift": "Drift",
    "DrumGroupDevice": "Drum Rack",
    "AudioEffectGroupDevice": "Audio Effect Rack",
    "InstrumentGroupDevice": "Instrument Rack",
}

# Older names the model may use; map to the current name.
ALIASES = {
    "chorus": "Chorus-Ensemble",
    "phaser": "Phaser-Flanger",
    "flanger": "Phaser-Flanger",
    "eq 8": "EQ Eight",
    "eq8": "EQ Eight",
    "eq 3": "EQ Three",
    "glue": "Glue Compressor",
    "frequency shifter": "Shifter",
    "auto-filter": "Auto Filter",
    "autofilter": "Auto Filter",
    "autopan": "Auto Pan",
    "vinyl": "Vinyl Distortion",
}

_LOWER = {name.lower(): name for name in STOCK_DEVICES}


def canonical(name: str | None) -> str | None:
    """Return the stock device name for a display name, class name or alias."""
    if not name:
        return None
    if name in STOCK_DEVICES:
        return name
    if name in CLASS_NAMES:
        return CLASS_NAMES[name]
    key = name.strip().lower()
    return _LOWER.get(key) or ALIASES.get(key)


def is_stock(name: str | None) -> bool:
    return canonical(name) is not None
