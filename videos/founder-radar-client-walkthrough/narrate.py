"""Sentence-timed offline narration and captions using the cached Kokoro model.

Requires kokoro_onnx, numpy and soundfile in an existing voice environment.
No provider login, remote upload or automatic installation.
Usage: python narrate.py OUTPUT_DIRECTORY
"""
import json
import re
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro

output = Path(sys.argv[1]).resolve()
scenes = json.loads((Path(__file__).parent / "scenes.json").read_text())
home = Path.home()
engine = Kokoro(str(home / ".cache/hyperframes/tts/models/kokoro-v1.0.onnx"),
                str(home / ".cache/hyperframes/tts/voices/voices-v1.0.bin"))
sr = 24000
audio, cues = [], []
clock = 0.0


def stamp(t):
    ms = round(t * 1000)
    return f"{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}"


for i, scene in enumerate(scenes):
    scene["start"] = clock
    for sentence in re.split(r"(?<=[.!?])\s+", scene["text"].strip()):
        samples, rate = engine.create(sentence, voice="bf_emma", speed=1.08, lang="en-gb")
        assert rate == sr
        active = np.flatnonzero(np.abs(samples) > .008)
        if len(active):
            samples = samples[max(0,active[0]-1200):min(len(samples),active[-1]+1800)]
        duration = len(samples) / sr
        # Short semantic caption chunks, proportional within this measured sentence.
        words = sentence.split()
        chunks = [words[j:j+11] for j in range(0,len(words),11)]
        cursor = clock
        for chunk in chunks:
            span = duration * len(chunk) / len(words)
            cues.append({"start": cursor, "end": cursor+span, "text":" ".join(chunk)})
            cursor += span
        audio.extend([samples,np.zeros(int(sr*.14),dtype=np.float32)])
        clock += duration + .14
    audio.append(np.zeros(int(sr*.45),dtype=np.float32));clock += .45
    scene["duration"] = clock - scene["start"]
    print(f"{i+1:02} {clock:.1f}s {scene['title']}", flush=True)
sf.write(str(output / "assets/narration.wav"), np.concatenate(audio), sr)
(output / "timeline.json").write_text(json.dumps({"duration":clock,"scenes":scenes,"captions":cues},indent=2))
(output / "captions.srt").write_text("\n\n".join(f"{i+1}\n{stamp(c['start'])} --> {stamp(c['end'])}\n{c['text']}" for i,c in enumerate(cues))+"\n")
(output / "TRANSCRIPT.md").write_text("# Founder Radar client walkthrough\n\n"+"\n\n".join(f"## {stamp(s['start'])} — {s['title']}\n\n{s['text']}" for s in scenes)+"\n")
print(f"Complete: {clock:.2f}s", flush=True)
