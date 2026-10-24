# Recording the demo

How the demo video in the README and on X is made. One take, edited in Cap, gives both.

## Tools

- **Cap** (free, `brew install --cask cap`) in Studio mode, with **Record System Audio** on so
  Live's sound is in the take. Its editor adds the background, the zooms and the captions.
- **ffmpeg** (`brew install ffmpeg`) for the copies below.
- `scripts/demo-media.sh` trims a raw take into a video and a GIF, if you skip Cap's editor.

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
- Cap: Studio mode, Display, system audio on.

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

Edit in Cap and export an MP4 (1080p, 30 fps, with audio). Then make two copies:

```sh
# For X: same picture, sound brought up to a normal streaming level.
ffmpeg -i cut.mp4 -c:v copy -af "loudnorm=I=-16:TP=-2,alimiter=limit=0.75:level=false" \
  -c:a aac -b:a 192k -movflags +faststart ad-x.mp4
# For the README: 720p, under 10 MB.
ffmpeg -i cut.mp4 -vf scale=1280:-2 -c:v libx264 -b:v 1300k -maxrate 1800k -bufsize 3600k \
  -af loudnorm=I=-16:TP=-1.5 -c:a aac -b:a 128k -movflags +faststart ad-readme.mp4
```

GitHub only plays a README video that was uploaded through its own editor. Drag the README
copy into any comment box on GitHub, copy the `https://github.com/user-attachments/assets/...`
link it inserts, and put that link on its own line in the README.
