"""Can Muse speak the scripted Vietnamese line, in the character's voice, consistently?

Runs four prompt variants on real Muse through the local gateway, then measures each take:
  V1  clean prompt: one short quoted line first, "say exactly this, nothing else"
  V2  V1 + an audio sample of the character's voice
  V3  V2 + an audio guide track of the exact line ("speak these words, lip-synced")
  V4  V1 prompts in ONE Muse chat (thread): character photos only on the first turn
Measures: words heard vs script (PhoWhisper, WER), voice similarity (WeSpeaker), identity (AI vision).

Usage (repo root):  .venv\\Scripts\\python scripts\\series_voice_experiment.py [--only V1,V4] [--out DIR]
Costs Muse generations (16 clips with all variants). Writes results JSON + the report.
"""
import argparse
import base64
import json
import mimetypes
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

LINES = [  # (character id, line) -- short lines, one speaker each
    ("suri", "Mẹ ơi, sao mặt trăng cứ đi theo con vậy?"),
    ("mother", "Tại mặt trăng thương con nhất nhà đó."),
    ("father", "Không đâu, bố mới là người đi theo con nè!"),
    ("suri", "Vậy bố là mặt trăng của con rồi!"),
]
ACTIONS = {
    "suri": "Suri looks up at the moon through the window, points at it and turns to talk",
    "mother": "Mother kneels beside Suri, smiles and answers her softly",
    "father": "Father pops in behind them with a playful grin and answers",
}
VOICE = {   # how each voice should sound, and the VieNeu preset used for samples / guide tracks
    "suri": ("a 4-year-old Vietnamese girl, high bright child voice, cheerful", "Ái Hân"),
    "mother": ("a warm Vietnamese woman about 30, soft gentle voice", "Mỹ Duyên"),
    "father": ("a cheerful Vietnamese man about 32, warm medium-low voice", "Mạnh Dũng"),
}
SAMPLE_TEXT = "Chào cả nhà, hôm nay trời đẹp quá, mình cùng ra công viên chơi nhé."
STYLE = ("Cute chibi 3D animation: big heads, small rounded bodies, huge expressive eyes, soft materials, "
         "warm light; cosy modern Vietnamese living room at night, full moon visible through the window.")


def env() -> dict:
    out = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def data_url(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or ("audio/wav" if path.suffix == ".wav" else "image/png")
    if path.suffix.lower() == ".webp":
        mime = "image/webp"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def character_refs() -> dict[str, Path]:
    data = json.loads((ROOT / "state" / "creative_templates.json").read_text(encoding="utf-8"))["templates"]
    tpl = data["suri-family"]
    tpl = tpl[-1] if isinstance(tpl, list) else tpl
    return {a["character_id"]: Path(a["path"]) for a in tpl.get("reference_assets") or []
            if Path(a["path"]).is_file()}


def names() -> dict[str, str]:
    return {"suri": "Suri", "mother": "Mother", "father": "Father"}


def prompt_for(variant: str, idx: int, cid: str, line: str, has_photo: bool, audio_note: str = "") -> str:
    name = names()[cid]
    look = f"{name} looks exactly like attachment 1." if has_photo else \
        f"{name} is the same character as in the previous video of this chat (same face, hair and outfit)."
    return (f'Spoken line (Vietnamese): "{line}"\n'
            f"{name} says exactly this line once, word for word, in clear natural Vietnamese, then stops talking. "
            "Nobody else speaks. No other words, no narration, no background music.\n"
            f"Voice: {VOICE[cid][0]}.{audio_note}\n"
            f"Scene: {STYLE} {look} Action: {ACTIONS[cid]}. Camera: medium close-up, slow push-in. "
            "Natural lip-sync for the line.")


class Gateway:
    def __init__(self, base: str, key: str):
        import httpx
        self.c = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {key}"}, timeout=180, trust_env=False)

    def submit(self, prompt: str, files: list[Path], thread_url: str | None = None, account_id: str | None = None) -> str:
        images = [{"url": data_url(p)} for p in files if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]
        audios = [{"url": data_url(p)} for p in files if p.suffix.lower() in (".wav", ".mp3")]
        # the embedded gateway makes one clip at a time: allow for the queue in front of this one
        body = {"prompt": prompt, "duration": 10, "size": "9:16", "wrapper": "minimal", "timeout": 5400}
        if images:
            body["images"] = images
        if audios:
            body["audios"] = audios
        if thread_url:
            body["thread_url"] = thread_url
        if account_id:
            body["account_id"] = account_id
        r = self.c.post("/v1/videos", json=body)
        r.raise_for_status()
        return r.json()["id"]

    def wait(self, tid: str, timeout: int = 6000) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            t = self.c.get(f"/v1/videos/{tid}").json()
            if t.get("status") in ("completed", "failed"):
                return t
            time.sleep(10)
        return {"status": "timeout"}

    def download(self, url: str, out: Path) -> Path:
        r = self.c.get(url) if url.startswith("/") else self.c.get(url.replace(str(self.c.base_url), ""))
        r.raise_for_status()
        out.write_bytes(r.content)
        return out


def prepare_audio(out: Path) -> tuple[dict, dict]:
    from agent_core.editor import tts
    samples, guides = {}, {}
    for cid, (_desc, preset) in VOICE.items():
        samples[cid] = out / f"voice_{cid}.wav"
        if not samples[cid].is_file():
            tts.synthesize(SAMPLE_TEXT, samples[cid], voice=preset)
    for i, (cid, line) in enumerate(LINES):
        guides[i] = out / f"guide_{i}.wav"
        if not guides[i].is_file():
            tts.synthesize(line, guides[i], voice=VOICE[cid][1])
    return samples, guides


def run_variant(gw: Gateway, variant: str, refs: dict, samples: dict, guides: dict, out: Path) -> list[dict]:
    takes = []

    def one(i, thread=None, photos=True, account=None):
        cid, line = LINES[i]
        files = [refs[cid]] if photos and cid in refs else []
        note = ""
        if variant in ("V2", "V3"):
            files.append(samples[cid])
            note = f" Attachment {len(files)} is an audio sample of {names()[cid]}'s voice: use exactly this voice."
        if variant == "V3":
            files.append(guides[i])
            note += (f" Attachment {len(files)} is an audio recording of the exact line: {names()[cid]} speaks "
                     "these exact words with the same timing, lip-synced, in that voice.")
        prompt = prompt_for(variant, i, cid, line, bool(files and photos and cid in refs), note)
        t0 = time.time()
        tid = gw.submit(prompt, files, thread_url=thread, account_id=account)
        res = gw.wait(tid)
        take = {"variant": variant, "index": i, "character": cid, "line": line, "task": tid,
                "status": res.get("status"), "seconds": round(time.time() - t0), "prompt": prompt,
                "thread_url": res.get("thread_url"), "account": res.get("account"), "error": res.get("error")}
        if res.get("status") == "completed" and res.get("url"):
            take["clip"] = str(gw.download(res["url"], out / f"{variant}_{i}.mp4"))
        return take

    if variant == "V4":     # one chat, in order; photos only on the first turn
        thread, account = None, None
        for i in range(len(LINES)):
            take = one(i, thread=thread, photos=(i == 0), account=account)
            thread = thread or take.get("thread_url")
            account = account or take.get("account")
            takes.append(take)
            if not thread:
                take["error"] = (take.get("error") or "") + " | no thread_url returned"
                break
        return takes
    with ThreadPoolExecutor(max_workers=len(LINES)) as pool:
        return list(pool.map(one, range(len(LINES))))


def analyse(takes: list[dict], samples: dict, refs: dict) -> dict:
    from agent_core.editor import speech_check as sc
    emb_sample = {cid: sc.voice_embedding(p) for cid, p in samples.items()}
    for t in takes:
        if not t.get("clip"):
            continue
        try:
            t.update(sc.check_line(Path(t["clip"]), t["line"], emb_sample[t["character"]]))
        except Exception as exc:  # noqa: BLE001
            t["check_error"] = str(exc)[:300]
    summary = {}
    for v in sorted({t["variant"] for t in takes}):
        mine = [t for t in takes if t["variant"] == v and "wer" in t]
        made = [t for t in takes if t["variant"] == v and t.get("clip")]
        suri = [t for t in mine if t["character"] == "suri" and t.get("speech_start") is not None]
        consistency = None
        if len(suri) == 2:
            try:
                a = sc.voice_embedding(Path(suri[0]["clip"]), start=suri[0]["speech_start"],
                                       length=max(0.8, suri[0]["speech_end"] - suri[0]["speech_start"]))
                b = sc.voice_embedding(Path(suri[1]["clip"]), start=suri[1]["speech_start"],
                                       length=max(0.8, suri[1]["speech_end"] - suri[1]["speech_start"]))
                consistency = sc.voice_similarity(a, b)
            except Exception:  # noqa: BLE001
                pass
        summary[v] = {
            "clips": len(made), "of": len([t for t in takes if t["variant"] == v]),
            "exact_lines (WER<=0.15)": sum(1 for t in mine if t["wer"] <= 0.15),
            "close_lines (WER<=0.35)": sum(1 for t in mine if t["wer"] <= 0.35),
            "mean_wer": round(sum(t["wer"] for t in mine) / len(mine), 3) if mine else None,
            "mean_voice_sim_to_sample": round(sum(t.get("voice_sim", 0) for t in mine) / len(mine), 3) if mine else None,
            "suri_voice_same_across_clips": consistency,
            "mean_seconds": round(sum(t["seconds"] for t in made) / len(made)) if made else None,
        }
    return summary


def identity_scores(takes: list[dict], refs: dict) -> None:
    """AI vision: does each take's character match the reference photo (0-10)?"""
    from agent_core.qa_video import clip_frames
    from agent_core.settings import get_settings
    from agent_core.workflow import _ask_json, _client
    s = get_settings()
    client = _client(s)
    for t in takes:
        if not t.get("clip") or t["character"] not in refs:
            continue
        frames = clip_frames(s, Path(t["clip"]), Path(t["clip"]).parent / "id_frames", points=(0.3, 0.7))
        prompt = (f"Ảnh cuối là ảnh tham chiếu nhân vật {names()[t['character']]}. Các ảnh trước là khung của một "
                  "clip. Nhân vật trong clip giống ảnh tham chiếu tới mức nào (mặt, tóc, trang phục, tạo hình)? "
                  'Trả DUY NHẤT JSON {"identity": 0-10, "note": "ngắn"}')
        try:
            raw = _ask_json(client, s, prompt, frames + [refs[t["character"]]], temperature=0.1, max_tokens=200)
            t["identity"] = raw.get("identity")
            t["identity_note"] = raw.get("note")
        except Exception as exc:  # noqa: BLE001
            t["identity_error"] = str(exc)[:200]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="V1,V2,V3,V4")
    ap.add_argument("--out", default="")
    ap.add_argument("--analyse-only", action="store_true")
    a = ap.parse_args()
    e = env()
    media = Path(e.get("AA_MEDIA_ROOT") or (ROOT / "media"))
    out = Path(a.out) if a.out else media / "experiments" / f"series-voice-{datetime.now():%Y%m%d-%H%M}"
    out.mkdir(parents=True, exist_ok=True)
    refs = character_refs()
    samples, guides = prepare_audio(out)
    results_file = out / "results.json"
    if a.analyse_only and results_file.is_file():
        takes = json.loads(results_file.read_text(encoding="utf-8"))["takes"]
    else:
        gw = Gateway(e.get("AA_VIDEOGEN_BASE_URL") or "http://127.0.0.1:18620", e["AA_VIDEOGEN_API_KEY"])
        variants = [v.strip() for v in a.only.split(",") if v.strip()]
        with ThreadPoolExecutor(max_workers=len(variants)) as pool:
            batches = list(pool.map(lambda v: run_variant(gw, v, refs, samples, guides, out), variants))
        takes = [t for b in batches for t in b]
        results_file.write_text(json.dumps({"takes": takes}, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = analyse(takes, samples, refs)
    identity_scores(takes, refs)
    for v in summary:
        ids = [t["identity"] for t in takes if t["variant"] == v and isinstance(t.get("identity"), (int, float))]
        summary[v]["mean_identity_0_10"] = round(sum(ids) / len(ids), 1) if ids else None
    results_file.write_text(json.dumps({"summary": summary, "takes": takes}, ensure_ascii=False, indent=1),
                            encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print("results:", results_file)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    sys.exit(main())
