# system.speech via powershell -- zero pip deps, works under app control
# sentiment drives rate/volume: angry caller -> slower + softer maya
from __future__ import annotations

import base64
import subprocess
from pathlib import Path

AGENT_VOICE = "Microsoft Zira Desktop"
CUSTOMER_VOICE = "Microsoft David Desktop"


def emotion_params(sentiment: float) -> tuple[int, int]:
    # sentiment -> (rate, volume)
    if sentiment < -0.5:
        return -2, 85    # calm, slower, empathetic under fire
    if sentiment > 0.3:
        return 1, 100    # upbeat, confident on the close
    return 0, 100


def synth(text: str, out_path: str | Path,
          voice: str = AGENT_VOICE, sentiment: float = 0.0) -> Path | None:
    # wav path, or None if the speech stack isn't there
    rate, volume = emotion_params(sentiment)
    safe = text.replace("'", "''")
    ps = (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"try {{ $s.SelectVoice('{voice}') }} catch {{}};"
        f"$s.Rate = {rate}; $s.Volume = {volume};"
        f"$s.SetOutputToWaveFile('{out_path}');"
        f"$s.Speak('{safe}'); $s.Dispose()"
    )
    enc = base64.b64encode(ps.encode("utf-16-le")).decode()
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-EncodedCommand", enc],
            check=True, capture_output=True, timeout=60)
    except Exception:
        return None
    p = Path(out_path)
    return p if p.exists() else None
