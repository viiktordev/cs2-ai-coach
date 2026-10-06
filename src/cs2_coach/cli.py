import argparse
import json
import math
import sys
import time
from pathlib import Path

from .events import EventTracker
from .vision import HudReader, validate_config
from .killfeed import KillFeedReader, KillFeedTracker
from .context import SideReader, build_context
from .bomb import BombReader, BombTracker
from .coaching import CoachingStream, dumps as compact_json


def frames(video, monitor, sample_fps, capture_region=None):
    import cv2
    if video:
        capture = cv2.VideoCapture(str(video))
        try:
            if not capture.isOpened():
                raise ValueError(f"Cannot open video: {video}")
            fps = capture.get(cv2.CAP_PROP_FPS)
            if fps <= 0:
                raise ValueError("Video FPS is unavailable")
            frame_id, next_sample = 0, 0.0
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                timestamp = frame_id / fps
                if timestamp >= next_sample:
                    yield frame_id, timestamp * 1000, frame
                    next_sample = timestamp + 1 / sample_fps
                frame_id += 1
        finally:
            capture.release()
    else:
        import mss
        import numpy as np
        with mss.mss() as screen:
            if monitor < 1 or monitor >= len(screen.monitors):
                raise ValueError(f"Monitor must be between 1 and {len(screen.monitors)-1}")
            area = dict(screen.monitors[monitor])
            if capture_region:
                x, y, width, height = capture_region
                if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > area["width"] or y + height > area["height"]:
                    raise ValueError("Capture region must fit inside the selected monitor")
                area = {"left": area["left"] + x, "top": area["top"] + y, "width": width, "height": height}
            start, frame_id = time.monotonic(), 0
            while True:
                tick = time.monotonic()
                frame = np.asarray(screen.grab(area))[:, :, :3].copy()
                yield frame_id, (tick-start)*1000, frame
                frame_id += 1
                time.sleep(max(0, 1/sample_fps - (time.monotonic()-tick)))


def main():
    parser = argparse.ArgumentParser(description="Stream visual HUD observations as JSONL")
    parser.add_argument("--config", type=Path, default=Path("config/hud.example.json"))
    parser.add_argument("--video", type=Path)
    parser.add_argument("--monitor", type=int, default=1)
    parser.add_argument("--capture-region", type=int, nargs=4, metavar=("X", "Y", "WIDTH", "HEIGHT"), help="Capture only this rectangle in pixels relative to the selected monitor")
    parser.add_argument("--sample-fps", type=float, default=5)
    parser.add_argument("--output", type=Path, help="Default: stdout")
    parser.add_argument('--format', choices=('coach', 'raw'), default='coach', help='Default: compact coaching JSONL; raw preserves full evidence/context')
    parser.add_argument('--raw-output', type=Path, help='Also save full raw events when using --format coach')
    parser.add_argument("--tesseract-cmd", help="Path to the installed Tesseract executable")
    parser.add_argument("--debug-dir", type=Path, help="Save frames, HUD crops and per-frame diagnostics locally")
    parser.add_argument('--verbose', action='store_true', help='With --debug-dir, also print periodic diagnostics to stderr')
    parser.add_argument("--duration", type=float, help="Stop automatically after this many seconds (wall clock)")
    parser.add_argument("--collect-killfeed", type=Path, help="Save timestamped kill feed crops every 0.5 seconds for calibration")
    parser.add_argument("--killfeed", action="store_true", help="Enable full kill-feed detection and local kill/death classification without a configured nickname")
    parser.add_argument("--killfeed-names", action="store_true", help="Also OCR names (optional, slower and not used for classification)")
    args = parser.parse_args()
    if args.video and args.capture_region:
        parser.error("--capture-region is only supported for screen capture")
    if args.killfeed_names and not args.killfeed:
        parser.error("--killfeed-names requires --killfeed")
    if args.raw_output and args.format != 'coach':
        parser.error('--raw-output requires --format coach')
    if args.output and args.raw_output and args.output.resolve() == args.raw_output.resolve():
        parser.error('--output and --raw-output must be different files')
    if args.video and any(path and path.resolve() == args.video.resolve() for path in (args.output, args.raw_output)):
        parser.error('Output must not overwrite the input video')
    if not 0 < args.sample_fps <= 60:
        parser.error("--sample-fps must be between 0 and 60")
    if args.duration is not None and (not math.isfinite(args.duration) or args.duration <= 0):
        parser.error("--duration must be a finite positive number")
    output = None
    diagnostics = None
    raw_output = None
    coaching = None
    last_timestamp = 0
    completion = 'complete'
    try:
        config = validate_config(json.loads(args.config.read_text(encoding="utf-8")))
        reader = HudReader(config["regions"], args.tesseract_cmd)
        side_reader = SideReader(reader.cv2, config.get("side_templates", {}), args.config.parent)
        tracker = EventTracker(config.get("confirmation_frames", 3))
        feed_reader = KillFeedReader(reader, read_names=args.killfeed_names) if args.killfeed else None
        feed_tracker = KillFeedTracker()
        bomb_reader = BombReader(reader.cv2, args.config.parent / config['bomb_template']) if config.get('bomb_template') else None
        bomb_tracker = BombTracker(config.get('confirmation_frames', 3))
        sequence = 0
        if args.debug_dir:
            args.debug_dir.mkdir(parents=True, exist_ok=True)
            diagnostics = (args.debug_dir / f"diagnostics-{time.time_ns()}.jsonl").open("w", encoding="utf-8")
        next_debug = 0.0
        if args.collect_killfeed:
            args.collect_killfeed.mkdir(parents=True, exist_ok=True)
        next_feed_capture = 0.0
        output = args.output.open("w", encoding="utf-8") if args.output else sys.stdout
        raw_output = args.raw_output.open('w', encoding='utf-8') if args.raw_output else None
        source = {"kind": "video", "path": str(args.video)} if args.video else {"kind": "screen", "monitor": args.monitor}
        if args.capture_region:
            source["capture_region"] = args.capture_region
        if args.format == 'coach':
            coaching = CoachingStream()
            detectors = ['hud'] + (['killfeed'] if feed_reader else []) + (['bomb'] if bomb_reader else [])
            print(compact_json(coaching.header(source, detectors)), file=output, flush=True)
        started = time.monotonic()
        for frame_id, timestamp, frame in frames(args.video, args.monitor, args.sample_fps, args.capture_region):
            if args.duration is not None and time.monotonic() - started >= args.duration:
                completion = 'duration_limit'
                break
            last_timestamp = timestamp
            readings = reader.read(frame)
            readings["side"] = side_reader.read(frame)
            bomb_reading = bomb_reader.read(frame, readings.get('round_timer')) if bomb_reader else None
            feed_rows = feed_reader.read(frame) if feed_reader else []
            debug_rows = [{key: value for key, value in row.items() if key != "fingerprint" and not key.startswith('_')} for row in feed_rows]
            diagnostic = {"frame_id": frame_id, "timestamp_ms": timestamp, "width": frame.shape[1], "height": frame.shape[0], "readings": readings, "killfeed": debug_rows, "detector": feed_reader.diagnostics if feed_reader else {"enabled": False}}
            diagnostic['bomb'] = bomb_reading
            if diagnostics:
                print(json.dumps(diagnostic, ensure_ascii=False), file=diagnostics, flush=True)
            if args.collect_killfeed and time.monotonic() >= next_feed_capture:
                height, width = frame.shape[:2]
                # Broad initial ROI; actual name/icon segmentation needs real examples.
                crop = frame[int(height * 0.06):int(height * 0.38), int(width * 0.50):width]
                filename = args.collect_killfeed / f"killfeed-{time.time_ns()}-{frame_id:06d}.png"
                if not reader.cv2.imwrite(str(filename), crop):
                    raise OSError(f"Cannot save kill feed crop: {filename}")
                next_feed_capture = time.monotonic() + 0.5
            if args.debug_dir and time.monotonic() >= next_debug:
                height, width = frame.shape[:2]
                images = {"frame": frame}
                for region in config["regions"]:
                    x, y, w, h = region["rect"]
                    images[region["name"]] = frame[int(y*height):int((y+h)*height), int(x*width):int((x+w)*width)]
                # Use indices for filenames: region names are user-configured labels.
                for index, (label, pixels) in enumerate(images.items()):
                    filename = "frame.png" if index == 0 else f"region-{index}.png"
                    if pixels.size and not reader.cv2.imwrite(str(args.debug_dir / filename), pixels):
                        raise OSError(f"Cannot save diagnostic image: {filename}")
                if args.verbose:
                    print(json.dumps({"debug": True, **diagnostic}, ensure_ascii=False), file=sys.stderr, flush=True)
                next_debug = time.monotonic() + 2
            events = tracker.update(readings, frame_id, timestamp, source)
            if feed_reader:
                events.extend(feed_tracker.update(feed_rows, frame_id, timestamp, source))
            if bomb_reading:
                events.extend(bomb_tracker.update(bomb_reading, frame_id, timestamp, source))
            context = build_context(tracker.observations(timestamp))
            for event in events:
                sequence += 1
                event["sequence"] = sequence
                event["context"] = context
                if event.get("field") == "side":
                    event["evidence"]["method"] = "team_emblem_template"
                if args.debug_dir and 'bbox' in event['evidence']:
                    x, y, w, h = event["evidence"]["bbox"]
                    evidence_path = args.debug_dir / f"event-{time.time_ns()}-{sequence}.png"
                    if not reader.cv2.imwrite(str(evidence_path), frame[y:y+h, x:x+w]):
                        raise OSError(f"Cannot save event evidence: {evidence_path}")
                    event["evidence"]["image_path"] = str(evidence_path.resolve())
                if raw_output:
                    print(json.dumps(event, ensure_ascii=False), file=raw_output, flush=True)
                if not coaching:
                    print(json.dumps(event, ensure_ascii=False), file=output, flush=True)
            if coaching:
                record = coaching.encode(events)
                if record:
                    print(compact_json(record), file=output, flush=True)
    except KeyboardInterrupt:
        completion = 'interrupted'
    except Exception as error:
        completion = 'error'
        print(f"Error: {error}", file=sys.stderr)
        return 1
    finally:
        if coaching and output:
            print(compact_json(coaching.finish(last_timestamp, completion)), file=output, flush=True)
        if raw_output:
            raw_output.close()
        if diagnostics:
            diagnostics.close()
        if output is not None and output is not sys.stdout:
            output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
