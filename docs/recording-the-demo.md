# Recording the demo

How the README GIF and the video for X are made. One take gives both: the GIF is the silent,
smaller cut, and the video keeps the sound.

## Tools

- **OBS Studio** (free, `brew install --cask obs`) to record the screen *with Live's sound*. On
  macOS 13 or later its **macOS Screen Capture** source can capture an app's audio, so no
  loopback driver is needed. macOS's own recorder (Cmd-Shift-5) can't record app sound, so it's
  only good for the GIF.
- **ffmpeg** (`brew install ffmpeg`), and **gifski** (`brew install gifski`) for a sharper GIF.
- `scripts/demo-media.sh` turns the take into `docs/media/demo.gif` and `docs/media/demo.mp4`.

## Setting up

**The set.** Pick the track the demo is about: a pad, keys or a lead that people can hear
getting wider. It must use only Live's own devices, so the answer stays on stock devices. Other
tracks can have missing plugins as long as the set still sounds like music. A track with a
missing instrument plays silence, so check by ear. Loop 8 or 16 bars of a full section.

**The screen.**

- Use one display, at a normal resolution. Turn on Do Not Disturb, and hide desktop icons and
  menu bar clutter (VPN, chat apps).
- Live: dark theme, zoom at 115 to 125 % (Settings > Look/Feel), browser hidden
  (Cmd-Opt-B), Detail View showing the selected track's devices.
- Panel: `scripts/panel.sh --data-dir ~/vibe2knob-demo`, so the take starts with no old chats.
  If the setup card shows, finish it once before recording. Dock the window on the right,
  about 420 px wide, dark theme.
- OBS: a macOS Screen Capture source for the display with Live's audio, a 1920 x 1080 canvas,
  60 fps, MP4 (Settings > Output > Recording Format).

**Dry runs.** Ask the question two or three times, starting a new chat each time, and record only
when an answer reads well. Answers vary from run to run, so the best take is a matter of picking
one.

## The script

About 25 seconds after editing. The loop plays the whole time.

| Time | On screen | The point |
|---|---|---|
| 0 to 3 s | The demo track is selected in Live. The panel's chip follows it. | It sees your set. |
| 3 to 6 s | Type "make it wider" and send. READING, then THINKING. | Plain words in. |
| 6 to 12 s | The answer streams in, then the step cards with their knob ranges. | Stock devices, real values, why. |
| 12 to 18 s | Move the mouse to Live and turn the knob from the first step to the middle of its range. | You turn the knobs. |
| 18 to 23 s | Click **I made the changes**. It reads the track again and answers with the next move. | It follows what you did. |
| 23 to 25 s | Hold still on the panel. | |

Move the mouse slowly and on purpose. If "wider" doesn't suit the track, use another word the
knowledge map knows (`vibe2knob terms`), like "warmer" or "punchier".

## Making the files

```sh
scripts/demo-media.sh ~/Movies/take.mp4 3.5 41 9 21
```

The arguments are the take, where to start and stop, and an optional stretch to play at 2x
(the wait for the model). The times are in the original take. The GIF must stay under 10 MB for
GitHub to show it; if it's bigger, cut the take shorter.
