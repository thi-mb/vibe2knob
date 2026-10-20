# Track-state format (v1)

This is the JSON the core reads: one Live track, its mixer settings and its device chain.
The Live bridge (AbletonOSC) prints it with `--json`, and the files in `fixtures/` follow it.
The model is in `src/vibe2knob/state.py`.

Only `track.name` and `track.type` are required. Every other field is optional, and
unknown fields are kept, so the bridge can add data before the core uses it.

```json
{
  "schema_version": 1,
  "tempo": 122.0,
  "notes": "Deep house track, the bass carries the low end.",
  "track": {
    "index": 2,
    "name": "Warm Pad",
    "type": "midi",
    "color": 16725558,
    "is_grouped": false,
    "mixer": { "volume": 0.85, "panning": 0.0, "mute": false, "solo": false, "arm": false },
    "devices": [
      {
        "index": 1,
        "name": "Chorus-Ensemble",
        "class_name": "Chorus2",
        "type": "audio_effect",
        "is_rack": false,
        "parameters": [
          { "index": 0, "name": "Device On", "value": 1.0, "min": 0.0, "max": 1.0, "is_quantized": true, "display": "On" },
          { "index": 6, "name": "Dry/Wet", "value": 0.25, "min": 0.0, "max": 1.0, "is_quantized": false, "display": "25 %" }
        ]
      }
    ]
  }
}
```

## Fields

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `1` | Bump when a change would break readers. |
| `tempo` | number | Song tempo in BPM. Optional. |
| `notes` | string | Free text from the user (genre, the track's role). Optional. |
| `track.name` | string | **Required.** |
| `track.type` | `audio` \| `midi` \| `group` \| `return` \| `master` | **Required.** |
| `track.index`, `track.color` | int | As Live reports them. |
| `track.is_grouped` | bool | The track sits inside a group track. |
| `track.mixer` | object | `volume` (raw fader 0..1), `panning` (-1..1), `mute`, `solo`, `arm`. |
| `track.devices` | list | Device chain in signal order. |
| `device.name` | string | Title bar name (users can rename devices). |
| `device.class_name` | string | Live's class, e.g. `Eq8`, `StereoGain` (Utility), `PluginDevice`. Used to recognise stock devices even when renamed. |
| `device.type` | `audio_effect` \| `instrument` \| `midi_effect` \| `rack` \| `unknown` | |
| `device.is_rack` | bool | |
| `device.chains` | list of `{name, devices}` | Rack chains; `devices` uses this same device shape, so racks can nest. |
| `device.parameters` | list | `name`, `value`, `min`, `max`, `is_quantized`, `display` (the value as Live shows it, e.g. `"120 Hz"`). The `Device On` parameter tells the core whether the device is switched on. |

## Notes

- Prefer sending `display`: "3.20 kHz" means more to the model than `0.55`.
- Third-party plugins (`PluginDevice`, `AuPluginDevice`) only expose `Device On` until their
  parameters are added with the plugin's Configure button in Live. The core tells the model the
  parameters are hidden, and keeps advice on stock devices.
- The core lists at most 40 parameters per device in the prompt and summarises the rest.
