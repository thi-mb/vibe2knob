#!/usr/bin/env bash
# Turn a screen recording of the demo into the README GIF and a video for X.
#   scripts/demo-media.sh take.mov                     # whole take
#   scripts/demo-media.sh take.mov 3.5 31              # keep 3.5 s to 31 s
#   scripts/demo-media.sh take.mov 3.5 31 9 15         # and play 9 s to 15 s at 2x (the model's wait)
# Writes docs/media/demo.gif (silent, 900 px wide) and docs/media/demo.mp4 (with sound, for X).
# Needs ffmpeg (brew install ffmpeg). Uses gifski for a better GIF when it's installed.
set -euo pipefail
cd "$(dirname "$0")/.."

in=${1:?usage: scripts/demo-media.sh take.mov [start end [fast_from fast_to]]}
start=${2:-0}
end=${3:-}
fast_from=${4:-}
fast_to=${5:-}
out=docs/media
mkdir -p "$out"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

has_audio=$(ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "$in" | head -1)

# 1. Trim, and optionally play one stretch at 2x. Times are in the original take.
trim=(-ss "$start")
[[ -n "$end" ]] && trim+=(-to "$end")
if [[ -n "$fast_from" && -n "$fast_to" ]]; then
  a=$(echo "$fast_from - $start" | bc)
  b=$(echo "$fast_to - $start" | bc)
  v="[0:v]trim=end=$a,setpts=PTS-STARTPTS[v1];[0:v]trim=start=$a:end=$b,setpts=(PTS-STARTPTS)/2[v2];[0:v]trim=start=$b,setpts=PTS-STARTPTS[v3]"
  if [[ -n "$has_audio" ]]; then
    v+=";[0:a]atrim=end=$a,asetpts=PTS-STARTPTS[a1];[0:a]atrim=start=$a:end=$b,asetpts=PTS-STARTPTS,atempo=2[a2];[0:a]atrim=start=$b,asetpts=PTS-STARTPTS[a3]"
    v+=";[v1][a1][v2][a2][v3][a3]concat=n=3:v=1:a=1[v][a]"
    maps=(-map "[v]" -map "[a]")
  else
    v+=";[v1][v2][v3]concat=n=3:v=1:a=0[v]"
    maps=(-map "[v]")
  fi
  ffmpeg -v error -y "${trim[@]}" -i "$in" -filter_complex "$v" "${maps[@]}" \
    -c:v libx264 -crf 12 -preset fast -c:a aac "$tmp/cut.mp4"
else
  ffmpeg -v error -y "${trim[@]}" -i "$in" -c:v libx264 -crf 12 -preset fast -c:a aac "$tmp/cut.mp4"
fi

# 2. Video for X: H.264 + AAC, at most 1920 px wide, 30 fps, plays before it's fully loaded.
ffmpeg -v error -y -i "$tmp/cut.mp4" -vf "scale='min(1920,iw)':-2,fps=30" \
  -c:v libx264 -profile:v high -pix_fmt yuv420p -crf 20 -c:a aac -b:a 160k \
  -movflags +faststart "$out/demo.mp4"

# 3. README GIF: silent, 900 px wide, 15 fps.
if command -v gifski >/dev/null; then
  ffmpeg -v error -y -i "$tmp/cut.mp4" -vf "fps=15,scale='min(900,iw)':-2:flags=lanczos" "$tmp/f%05d.png"
  gifski --quiet --fps 15 --quality 85 -o "$out/demo.gif" "$tmp"/f*.png
else
  ffmpeg -v error -y -i "$tmp/cut.mp4" -vf "fps=15,scale='min(900,iw)':-2:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4" "$out/demo.gif"
fi

ls -lh "$out/demo.gif" "$out/demo.mp4"
echo "GitHub shows GIFs up to 10 MB. If demo.gif is bigger, trim the take or drop the fps to 12."
