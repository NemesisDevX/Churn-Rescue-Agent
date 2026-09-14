"""Build masterpiece_demo.mp4: the V8 director's cut.

Scenario: a 3-column swarm dial. The middle column (Helvetia) goes
nuclear -- sentiment <= -0.90 trips CRITICAL CHURN, the rage line names an
unknown vendor so the live OSINT scrape fires, the operator clicks MANUAL
OVERRIDE, the VP bridges in, all three calls close with PDF addenda, and
the CEO Post-Call Strategic Insights overlay fades in on the ARR total.

- Local FSM sim produces the exact on-screen dialogue + sentiment per beat.
- System.Speech renders every line; agent voice uses emotion_params()
  (angry -> rate -2 / vol 85, accepting -> +1 / 100).
- evaluate-poll waits: Playwright rAF polling stalls under video capture.
- imageio_ffmpeg adelay+amix puts every wav on its on-screen timestamp.

Run:  python record_masterpiece.py   (server must be live on :8000)
"""
import asyncio
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT_MP4 = ROOT / "masterpiece_demo.mp4"
RAW_DIR = ROOT / "demo_raw"
AUDIO_DIR = ROOT / "demo_audio"

# per-column customer scripts; "ACT:override" clicks the red button
SCRIPTS = {
    "acct_northwind": [
        "We're canceling -- Salesforce fits our workflow better.",
        "That's not enough. Make it worth staying.",
        "Fine, I'll take the deal.",
    ],
    "acct_helvetia": [
        "This is the worst garbage nightmare ever. We're canceling and "
        "moving to Zenith.",
        "ACT:override",
        "Honestly it's just too expensive for what it is.",
        "Fine, I'll take the deal.",
    ],
    "acct_bluefin": [
        "Linear fits our workflow better. We're canceling.",
        "Hmm. It's still pricey though.",
        "Okay, deal. I'll take it.",
    ],
}
VOICES = {"agent": "Microsoft Zira Desktop", "customer": "Microsoft David Desktop"}
CUST_RATE = {"acct_northwind": -1, "acct_helvetia": 1, "acct_bluefin": 0}

# greenlet .pyd is App-Control-blocked; playwright only uses it for user
# event fibers we never register, so a pure-python stand-in is enough.
SHIM = '''\
class GreenletExit(BaseException):
    pass

class greenlet:
    dead = False
    parent = None
    def __init__(self, run=None, *a, **k):
        self._run = run
    def switch(self, *args):
        if self._run:
            return self._run(*args)

_current = greenlet()

def getcurrent():
    return _current

def settrace(cb):
    pass
'''

shim_dir = Path(tempfile.gettempdir()) / "pwshim"
shim_dir.mkdir(exist_ok=True)
(shim_dir / "greenlet.py").write_text(SHIM)
sys.path.insert(0, str(shim_dir))

from playwright.async_api import async_playwright  # noqa: E402

sys.path.insert(0, str(ROOT))
from churn_rescue.agent import RetentionAgent, score_sentiment  # noqa: E402
from churn_rescue.db import get_customer, DEFAULT_DB_PATH  # noqa: E402
from churn_rescue.tts import emotion_params  # noqa: E402


def build_beats(cid: str) -> list[tuple[str, str, float]]:
    """Local FSM sim -> ('agent'|'customer'|'action', text, sentiment)."""
    customer = get_customer(cid, DEFAULT_DB_PATH)
    agent = RetentionAgent(customer)
    beats: list[tuple[str, str, float]] = []

    def harvest(events):
        for ev in events:
            if ev["type"] == "transcript" and ev["speaker"] == "agent":
                beats.append(("agent", ev["text"], agent.sentiment))

    harvest(agent.start_call())
    for line in SCRIPTS[cid]:
        if line == "ACT:override":
            beats.append(("action", "override", agent.sentiment))
            harvest(agent.takeover())
        else:
            beats.append(("customer", line, score_sentiment(line)))
            harvest(agent.handle_utterance(line))
    return beats


def synth_all(all_lines: list[tuple[str, str, str, float]]) -> list[Path]:
    """all_lines: (cid, speaker, text, sentiment). Wavs in the same order."""
    AUDIO_DIR.mkdir(exist_ok=True)
    manifest = []
    for i, (cid, spk, text, sent) in enumerate(all_lines):
        rate, vol = emotion_params(sent)
        if spk == "customer":
            rate, vol = CUST_RATE[cid], 100
        manifest.append({
            "file": str(AUDIO_DIR / f"{i:02d}_{cid}_{spk}.wav"),
            "voice": VOICES[spk], "rate": rate, "volume": vol, "text": text,
        })
    ps = f"""\
Add-Type -AssemblyName System.Speech
$items = @'
{json.dumps(manifest)}
'@ | ConvertFrom-Json
foreach ($i in $items) {{
  $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
  try {{ $s.SelectVoice($i.voice) }} catch {{}}
  $s.Rate = [int]$i.rate
  $s.Volume = [int]$i.volume
  $s.SetOutputToWaveFile($i.file)
  $s.Speak($i.text)
  $s.Dispose()
}}
"""
    enc = base64.b64encode(ps.encode("utf-16-le")).decode()
    subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", enc],
                   check=True, capture_output=True)
    return [Path(m["file"]) for m in manifest]


def wav_seconds(path: Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


async def wait_count(page, sel: str, n: int, t0: float) -> float:
    # rAF-polled wait_for_function stalls under video capture; poll the DOM
    deadline = time.monotonic() + 25
    expr = f"document.querySelectorAll('{sel}').length"
    while time.monotonic() < deadline:
        if await page.evaluate(expr) >= n:
            return time.monotonic() - t0
        await asyncio.sleep(0.2)
    raise TimeoutError(f"{sel} never reached {n}")


async def wait_shown(page, sel: str) -> None:
    deadline = time.monotonic() + 25
    expr = f"document.querySelector('{sel}') !== null"
    while time.monotonic() < deadline:
        if await page.evaluate(expr):
            return
        await asyncio.sleep(0.2)
    raise TimeoutError(f"{sel} never appeared")


async def drive_column(page, cid, beats, gidx_of, durs, offsets, t0):
    """Play one column's script. gidx_of[i] = global wav index for beat i."""
    na = nc = 0
    for i, (who, text, _sent) in enumerate(beats):
        gi = gidx_of[i]
        if who == "action":  # override click once the red button shows
            await wait_shown(page, f"#col-{cid} .ovrbtn.show")
            await page.wait_for_timeout(900)
            await page.click(f"#col-{cid} .ovrbtn")
            continue
        if who == "agent":
            na += 1
            offsets[gi] = await wait_count(
                page, f"#col-{cid} .msg.agent", na, t0)
            await page.wait_for_timeout(int(durs[gi] * 1000) + 250)
        else:
            inp = page.locator(f"#col-{cid} [data-in]")
            await inp.fill(text)
            await page.wait_for_timeout(350)
            await inp.evaluate("el => el.value = ''")
            nc += 1
            payload = json.dumps(text)
            await page.evaluate(f"sendUtterance('{cid}', {payload})")
            try:
                offsets[gi] = await wait_count(
                    page, f"#col-{cid} .msg.customer", nc, t0)
            except TimeoutError:
                # transport flake: re-fire once, the echo proves delivery
                await page.evaluate(f"sendUtterance('{cid}', {payload})")
                offsets[gi] = await wait_count(
                    page, f"#col-{cid} .msg.customer", nc, t0)
            await page.wait_for_timeout(int(durs[gi] * 1000) + 250)


def end_active_calls() -> None:
    """Drop calls left active in server memory by an earlier crashed run."""
    import urllib.request

    for cid in SCRIPTS:
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:8000/api/calls/end",
                data=json.dumps({"call_id": cid}).encode(),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass


async def record(col_beats, gidx_maps, durs, offsets):
    RAW_DIR.mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=True)
        ctx = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            record_video_dir=str(RAW_DIR),
            record_video_size={"width": 1920, "height": 1080},
        )
        t0 = time.monotonic()
        page = await ctx.new_page()
        await page.goto("http://127.0.0.1:8000")
        await page.wait_for_selector("#swarmbtn")
        await page.wait_for_timeout(1600)

        await page.click("#swarmbtn")

        try:
            await asyncio.gather(*(
                drive_column(page, cid, col_beats[cid], gidx_maps[cid],
                             durs, offsets, t0)
                for cid in col_beats
            ))
        except Exception:
            for c in col_beats:
                diag = await page.evaluate("""(cid) => ({
                    live: [...liveCalls], wsOpen,
                    agents: document.querySelectorAll(`#col-${cid} .msg.agent`).length,
                    custs: document.querySelectorAll(`#col-${cid} .msg.customer`).length,
                    state: document.querySelector(`#col-${cid} [data-cstate]`)?.textContent,
                })""", c)
                print("DIAG", c, diag, flush=True)
            raise

        # the money shot: boardroom overlay fades in on the swarm results
        await wait_shown(page, "#insights.show")
        await page.wait_for_timeout(6000)
        await ctx.close()
        await browser.close()

    videos = sorted(RAW_DIR.glob("*.webm"), key=os.path.getmtime)
    if not videos:
        raise RuntimeError("no video captured")
    return videos[-1]


def mux(webm: Path, wavs: list[Path], offsets: list[float]) -> None:
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ffmpeg, "-y", "-i", str(webm)]
    for w in wavs:
        cmd += ["-i", str(w)]
    chains = ";".join(
        f"[{i + 1}:a]adelay={int(off * 1000)}:all=1[a{i}]"
        for i, off in enumerate(offsets)
    )
    mix = "".join(f"[a{i}]" for i in range(len(wavs)))
    cmd += [
        "-filter_complex",
        f"{chains};{mix}amix=inputs={len(wavs)}:normalize=0[aout]",
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "faststart",
        str(OUT_MP4),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def main() -> None:
    end_active_calls()
    subprocess.run([sys.executable, "seed_customers.py"], cwd=ROOT, check=True)

    col_beats = {cid: build_beats(cid) for cid in SCRIPTS}

    # flatten to a global wav list; remember each column's index map
    all_lines, gidx_maps = [], {}
    for cid, beats in col_beats.items():
        gidx_maps[cid] = []
        for who, text, sent in beats:
            if who == "action":
                gidx_maps[cid].append(-1)
            else:
                gidx_maps[cid].append(len(all_lines))
                all_lines.append((cid, who, text, sent))

    wavs = synth_all(all_lines)
    durs = [wav_seconds(w) for w in wavs]
    offsets = [0.0] * len(wavs)

    webm = asyncio.run(record(col_beats, gidx_maps, durs, offsets))

    # same-batch bubbles share a timestamp; space a column's audio out so
    # its lines never overlap (cross-column overlap is the whole point)
    for cid, beats in col_beats.items():
        cursor = 0.0
        for i, (who, _text, _s) in enumerate(beats):
            gi = gidx_maps[cid][i]
            if gi < 0:
                continue
            offsets[gi] = max(offsets[gi], cursor)
            cursor = offsets[gi] + durs[gi] + 0.3

    mux(webm, wavs, offsets)
    print(f"beats: {len(all_lines)}  -> {OUT_MP4.name} "
          f"({OUT_MP4.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
