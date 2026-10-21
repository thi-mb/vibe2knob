"""Track-state input format (v1).

This is the contract between the Live bridge (AbletonOSC dump) and the core.
It is documented in docs/track-state-format.md; keep the two in sync.
Unknown fields are kept, so the bridge can add data before the core uses it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .stock_devices import canonical

TrackType = Literal["audio", "midi", "group", "return", "master"]
DeviceType = Literal["audio_effect", "instrument", "midi_effect", "rack", "unknown"]

# Parameters past this count are summarized rather than listed in the prompt.
MAX_PARAMS_PER_DEVICE = 40


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class Parameter(_Open):
    index: int | None = None
    name: str
    value: float | int | str | bool | None = None
    min: float | None = None
    max: float | None = None
    is_quantized: bool | None = None
    display: str | None = Field(
        default=None, description="Value as Live shows it, e.g. '120 Hz' or '35 %'."
    )

    def shown(self) -> str:
        if self.display:
            return self.display
        if self.value is None:
            return "?"
        if self.min is not None and self.max is not None:
            return f"{self.value} (range {self.min}..{self.max})"
        return str(self.value)


PLUGIN_CLASSES = {"PluginDevice", "AuPluginDevice", "MxDeviceAudioEffect", "MxDeviceInstrument",
                  "MxDeviceMidiEffect"}


class Device(_Open):
    index: int | None = None
    name: str = Field(description="Name shown in the device title bar (may be renamed).")
    class_name: str | None = Field(
        default=None, description="Live's internal class, e.g. 'Eq8' or 'StereoGain'."
    )
    type: DeviceType = "unknown"
    is_rack: bool = False
    parameters: list[Parameter] = Field(default_factory=list)
    chains: list["Chain"] = Field(default_factory=list, description="Rack chains, if any.")

    @property
    def enabled(self) -> bool:
        for p in self.parameters:
            if p.name == "Device On":
                return bool(p.value)
        return True

    @property
    def is_plugin(self) -> bool:
        """Third-party plugin or Max for Live device, i.e. not a stock Live device."""
        return self.class_name in PLUGIN_CLASSES


class Chain(_Open):
    name: str
    devices: list[Device] = Field(default_factory=list)


class Mixer(_Open):
    volume: float | None = Field(default=None, description="Live's raw fader value, 0..1.")
    panning: float | None = Field(default=None, description="-1 (left) .. 1 (right).")
    mute: bool | None = None
    solo: bool | None = None
    arm: bool | None = None


class Track(_Open):
    index: int | None = None
    name: str
    type: TrackType
    color: int | None = None
    is_grouped: bool | None = None
    mixer: Mixer | None = None
    devices: list[Device] = Field(default_factory=list)


class TrackState(_Open):
    schema_version: Literal[1] = 1
    live_version: str | None = None
    tempo: float | None = None
    track: Track
    notes: str | None = Field(
        default=None, description="Optional free text from the user, e.g. genre or role."
    )

    @classmethod
    def load(cls, path: str | Path) -> "TrackState":
        return cls.from_json(Path(path).read_text())

    @classmethod
    def from_json(cls, text: str) -> "TrackState":
        return cls.model_validate(json.loads(text))

    def all_devices(self) -> list[Device]:
        out: list[Device] = []

        def walk(devices: list[Device]) -> None:
            for d in devices:
                out.append(d)
                for c in d.chains:
                    walk(c.devices)

        walk(self.track.devices)
        return out

    def describe(self) -> str:
        """Compact, readable summary for the prompt."""
        t = self.track
        head = [f"Track: \"{t.name}\" ({t.type} track)"]
        if self.live_version:
            head.append(f"Live version: {self.live_version}")
        if self.tempo:
            head.append(f"Tempo: {self.tempo:g} BPM")
        if t.is_grouped:
            head.append("This track is inside a group.")
        if t.mixer and t.mixer.mute:
            head.append("The track is muted.")
        if self.notes:
            head.append(f"User notes: {self.notes}")
        lines = head + ["Device chain (signal flows top to bottom):"]
        if not t.devices:
            lines.append("  (empty, no devices)")
        _describe_devices(t.devices, lines, indent=1)
        return "\n".join(lines)


def _describe_devices(devices: list[Device], lines: list[str], indent: int) -> None:
    pad = "  " * indent
    for i, d in enumerate(devices, 1):
        lines.append(f"{pad}{i}. {d.name}{'' if d.enabled else ' (OFF)'}")
        if d.is_plugin:
            lines.append(f"{pad}   note: third-party plugin or Max device; values are raw 0..1")
        else:
            stock = canonical(d.class_name) or canonical(d.name)
            if stock and stock != d.name:
                lines.append(f"{pad}   note: this is Live's {stock}, renamed")
        params = [p for p in d.parameters if p.name != "Device On"]
        if d.is_plugin and not params:
            lines.append(f"{pad}   - parameters not visible (not configured in Live)")
        for p in params[:MAX_PARAMS_PER_DEVICE]:
            lines.append(f"{pad}   - {p.name}: {p.shown()}")
        if len(params) > MAX_PARAMS_PER_DEVICE:
            lines.append(f"{pad}   - ... {len(params) - MAX_PARAMS_PER_DEVICE} more parameters")
        for c in d.chains:
            lines.append(f"{pad}   chain \"{c.name}\":")
            _describe_devices(c.devices, lines, indent + 2)


Device.model_rebuild()
