#!/usr/bin/env python3
import json, subprocess, shlex, os, re
from pathlib import Path

US = 1_000_000.0

def us_to_s(us): 
    return float(us) / US

def atempo_chain(speed: float) -> str:
    filters = []
    if speed <= 0: 
        return "atempo=1.0"
    while speed < 0.5:
        filters.append("atempo=0.5")
        speed /= 0.5
    while speed > 2.0:
        filters.append("atempo=2.0")
        speed /= 2.0
    filters.append(f"atempo={speed:.3f}")
    return ",".join(filters)

def load_json(p): 
    with open(p, "r", encoding="utf-8") as f: 
        return json.load(f)

def resolve_path(path, meta):
    m = re.match(r"^##_?draftpath_placeholder_[A-F0-9-]+_##[\\/](.*)$", path, re.I)
    if m:
        suffix = m.group(1).replace("\\", os.sep).replace("/", os.sep)
        base = meta.get("draft_fold_path") or meta.get("draft_root_path") or ""
        return str(Path(base) / suffix)
    return path

def build_command(content_path, meta_path, out_path):
    content = load_json(content_path)
    meta = load_json(meta_path)

    width = int(content.get("canvas_config", {}).get("width", 1920))
    height = int(content.get("canvas_config", {}).get("height", 1080))
    fps = float(content.get("fps", 30.0))

    vids = {v["id"]: v for v in content.get("materials", {}).get("videos", [])}
    imgs = {i["id"]: i for i in content.get("materials", {}).get("images", [])}

    seg_filters = []
    v_labels, a_labels = [], []
    input_files = []

    # --- VideoChinh ---
    track_main = next((t for t in content.get("tracks", []) if t.get("name") == "VideoChinh"), None)
    if not track_main:
        raise RuntimeError("Không tìm thấy track VideoChinh")

    main_seg = track_main["segments"][0]
    main_mat = vids[main_seg["material_id"]]
    main_path = resolve_path(main_mat["path"], meta)
    input_files.append(main_path)

    for i, seg in enumerate(track_main["segments"]):
        mat = vids[seg["material_id"]]
        path = resolve_path(mat["path"], meta)

        src_start = us_to_s(int(seg["source_timerange"].get("start", 0)))
        src_dur = us_to_s(int(seg["source_timerange"].get("duration", 0)))
        tgt_dur = us_to_s(int(seg["target_timerange"].get("duration", 0)))

        # tính speed thực
        speed = src_dur / tgt_dur if tgt_dur > 0 else 1.0

        # video filter
        seg_filters.append(
            f"[0:v]trim=start={src_start:.3f}:end={src_start+src_dur:.3f},setpts=PTS-STARTPTS,"
            f"setpts=PTS/{speed},scale={width}:{height}[v{i}]"
        )
        v_labels.append(f"[v{i}]")

        # audio filter
        seg_filters.append(
            f"[0:a]atrim=start={src_start:.3f}:end={src_start+src_dur:.3f},asetpts=N/SR/TB,"
            f"{atempo_chain(speed)}[a{i}]"
        )
        a_labels.append(f"[a{i}]")

    n = len(v_labels)
    pairs = "".join(v + a for v, a in zip(v_labels, a_labels))
    seg_filters.append(pairs + f"concat=n={n}:v=1:a=1[vmain][aout]")

    final_v = "[vmain]"

    # --- Overlay ---
    for tr in content.get("tracks", []):
        if tr.get("name") != "Overlay":
            continue
        for j, seg in enumerate(tr.get("segments", [])):
            mat_id = seg["material_id"]
            ov_path = None
            if mat_id in imgs:
                ov_path = resolve_path(imgs[mat_id]["path"], meta)
            elif mat_id in vids:
                ov_path = resolve_path(vids[mat_id]["path"], meta)
            if not ov_path:
                continue
            input_files.append(ov_path)
            idx = len(input_files) - 1

            tgt_start = us_to_s(int(seg["target_timerange"].get("start", 0)))
            tgt_dur = us_to_s(int(seg["target_timerange"].get("duration", 0)))
            tgt_end = tgt_start + tgt_dur

            seg_filters.append(f"[{idx}:v]scale={width}:{height}[ov{j}]")

            if tgt_dur <= 0.001:  # overlay suốt video
                seg_filters.append(f"{final_v}[ov{j}]overlay=0:0[vtmp{j}]")
            else:
                seg_filters.append(
                    f"{final_v}[ov{j}]overlay=0:0:enable='between(t,{tgt_start:.3f},{tgt_end:.3f})'[vtmp{j}]"
                )
            final_v = f"[vtmp{j}]"

    filter_complex = ";".join(seg_filters)

    cmd = ["ffmpeg", "-y"]
    for f in input_files:
        cmd += ["-i", f]
    cmd += [
        "-filter_complex", filter_complex,
        "-map", final_v, "-map", "[aout]",
        "-r", str(fps),
        "-c:v", "h264_nvenc", "-preset", "p5", "-b:v", "5M",
        "-c:a", "aac", "-b:a", "192k",
        out_path
    ]

    return cmd

if __name__ == "__main__":
    cmd = build_command("draft_content.json", "draft_meta_info.json", "render_full.mp4")
    print("FFmpeg command:\n", " ".join(shlex.quote(c) for c in cmd))
    subprocess.run(cmd)
