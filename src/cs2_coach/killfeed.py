"""Visual kill/death heuristics calibrated on a live player's CS2 HUD."""

from datetime import datetime, timezone


class KillFeedReader:
    def __init__(self, hud_reader, read_names=False):
        self.cv2 = hud_reader.cv2
        self.ocr = hud_reader.ocr
        self.read_names = read_names
        self.diagnostics = {}

    def read(self, frame):
        import numpy as np
        cv2 = self.cv2
        height, width = frame.shape[:2]
        scale = width / 1280
        ox, oy = int(width * 0.50), int(height * 0.06)
        roi = frame[oy:int(height * 0.38), ox:width]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        red = cv2.inRange(hsv, (0, 90, 65), (10, 255, 255)) | cv2.inRange(hsv, (170, 90, 65), (179, 255, 255))
        # Repair a few broken pixels in a thin outline before extracting rectangles.
        kernel = np.ones((max(2, round(3*scale)), max(2, round(3*scale))), np.uint8)
        red = cv2.morphologyEx(red, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(red, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        # Local death sample has a muted red filled notification and a red death view.
        full_hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        overlay = cv2.inRange(full_hsv, (0, 65, 25), (12, 255, 255)) | cv2.inRange(full_hsv, (172, 65, 25), (179, 255, 255))
        death_view = np.count_nonzero(overlay) / overlay.size > 0.55
        candidates = [(contour, "player.kill") for contour in contours]
        # Find notification backgrounds by sampling their right padding.
        # Matching sampled colors tolerates brightness changes.
        sampled = set()
        sample_x = max(0, roi.shape[1]-round(15*scale))
        for sample_y in range(0, roi.shape[0], max(1, round(8*scale))):
            hue, saturation, value = map(int, hsv[sample_y, sample_x])
            reddish = (hue <= 14 or hue >= 170) and saturation >= 60
            if not (value >= 25 and (reddish or saturation < 100)):
                continue
            color = roi[sample_y, sample_x].astype(np.int16)
            key = tuple(color // 8)
            if key in sampled:
                continue
            sampled.add(key)
            filled = cv2.inRange(roi, np.maximum(color-8, 0).astype(np.uint8), np.minimum(color+8, 255).astype(np.uint8))
            filled = cv2.morphologyEx(filled, cv2.MORPH_OPEN, kernel)
            background_contours, _ = cv2.findContours(filled, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            kind = "player.death" if reddish and death_view else "killfeed.kill"
            candidates.extend((contour, kind) for contour in background_contours)
        # Standard feed rows have a stable baseline even when their translucent
        # backgrounds vary. Locate small colored glyphs, then require a white
        # weapon-like component in the gap between the participant groups.
        for row_index in range(8):
            top = round((67+30*row_index)*height/960)-oy
            row_height = round(27*height/960)
            if top < 0 or top+row_height > roi.shape[0]:
                continue
            band = hsv[top+2:top+row_height-2]
            glyphs = self._name_mask(band)
            count, labels, stats, _ = cv2.connectedComponentsWithStats(glyphs)
            letters = np.zeros_like(glyphs)
            for label in range(1, count):
                gx, gy, gw, gh, area = stats[label]
                if 1*scale <= gw <= 120*scale and 5*scale <= gh <= 17*scale and area >= 4*scale*scale:
                    letters[labels == label] = 255
            columns = np.flatnonzero(np.any(letters, axis=0))
            if len(columns) == 0:
                continue
            groups = np.split(columns, np.flatnonzero(np.diff(columns) > 20*scale)+1)
            groups = [group for group in groups if len(group) >= 8*scale]
            if len(groups) < 2 or groups[-1][-1] < roi.shape[1]-45*scale:
                continue
            groups = groups[-2:]
            gap_left, gap_right = int(groups[-2][-1])+1, int(groups[-1][0])
            gap = band[:, gap_left:gap_right]
            if gap.size == 0:
                continue
            white = cv2.inRange(gap, (0, 0, 160), (179, 65, 255))
            icon_contours, _ = cv2.findContours(white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not any(cv2.boundingRect(c)[2] >= 10*scale and cv2.boundingRect(c)[3] >= 5*scale for c in icon_contours):
                continue
            left = max(0, int(groups[0][0])-round(10*scale))
            right = roi.shape[1]-round(9*scale)
            area = band[:, left:right]
            if np.count_nonzero(area[:, :, 2] < 160)/area[:, :, 2].size < 0.45:
                continue
            contour = np.array([[[left, top]], [[right-1, top]], [[right-1, top+row_height-1]], [[left, top+row_height-1]]], dtype=np.int32)
            candidates.append((contour, "killfeed.kill"))
        rows = []
        self.diagnostics = {"contours": len(candidates), "rectangles": 0, "content_rejected": 0, "death_view": bool(death_view)}
        candidates.sort(key=lambda item: (item[1] == "killfeed.kill", -cv2.boundingRect(item[0])[2]))
        for contour, event_type in candidates:
            x, y, w, h = cv2.boundingRect(contour)
            if not (90 * scale <= w <= width * 0.49 and 18 * scale <= h <= 45 * scale):
                continue
            # Notifications share the right edge and a 30 px row grid. Scene
            # geometry farther down the ROI must not become a notification.
            row_number = round(((oy+y)*960/height-67)/30)
            if not 0 <= row_number < 8 or abs((oy+y)*960/height-(67+30*row_number)) > 3:
                continue
            if abs((ox+x+w)*1280/width-1271) > 4:
                continue
            edge = max(1, round(2 * scale))
            mask = red[y:y+h, x:x+w]
            if event_type == "player.kill":
                if np.count_nonzero(mask[:edge]) / (edge*w) < 0.35 or np.count_nonzero(mask[-edge:]) / (edge*w) < 0.35:
                    continue
                if np.count_nonzero(mask[edge:-edge, edge:-edge]) / ((h-2*edge)*(w-2*edge)) > 0.45:
                    continue
            elif x+w < roi.shape[1]*0.95 or cv2.contourArea(contour)/(w*h) < 0.65:
                continue
            crop = roi[y+edge:y+h-edge, x+edge:x+w-edge]
            if any(abs(row["bbox"][1]-(oy+y)) < 4*scale for row in rows):
                continue
            self.diagnostics["rectangles"] += 1
            colors = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            names = self._name_mask(colors)
            # Bright icon/text content is required, but name segmentation is not.
            peak = int(colors[:, :, 2].max())
            white = cv2.inRange(colors, (0, 0, max(70, round(peak*0.70))), (179, 90, 255))
            if np.count_nonzero(white) < 8*scale*scale or np.count_nonzero(names | white) < 35*scale*scale:
                self.diagnostics["content_rejected"] += 1
                continue
            if event_type == "killfeed.kill" and np.count_nonzero(names) < 15*scale*scale:
                self.diagnostics["content_rejected"] += 1
                continue
            occupied = np.flatnonzero(np.any(names != 0, axis=0))
            groups = np.split(occupied, np.flatnonzero(np.diff(occupied) > 20 * scale) + 1) if len(occupied) else []
            if event_type == "killfeed.kill" and len(groups) < 2:
                self.diagnostics["content_rejected"] += 1
                continue
            # Also require a weapon between the participants for background
            # candidates; colored scenery and a bright patch are insufficient.
            if event_type == "killfeed.kill":
                gap = white[:, int(groups[-2][-1])+1:int(groups[-1][0])]
                icons, _ = cv2.findContours(gap, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                if not any(cv2.boundingRect(c)[2] >= 10*scale and cv2.boundingRect(c)[3] >= 5*scale for c in icons):
                    continue
            participants = []
            for group in (groups if self.read_names and len(groups) == 2 else []):
                if len(group) < 5 * scale:
                    continue
                left, right = int(group[0]), int(group[-1])+1
                binary = names[:, left:right]
                ys = np.flatnonzero(np.any(binary != 0, axis=1))
                binary = binary[ys[0]:ys[-1]+1]
                binary = cv2.resize(binary, None, fx=5, fy=5, interpolation=cv2.INTER_CUBIC)
                binary = cv2.copyMakeBorder(255-binary, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
                name = self.ocr.image_to_string(binary, config="--psm 8").strip()
                if name:
                    participants.append({"name": name, "x": ox+x+edge+left})
            # Hash only bright foreground glyphs. The translucent background
            # changes as the camera moves and must not define message identity.
            foreground = cv2.inRange(colors, (0, 0, max(80, round(peak*0.65))), (179, 255, 255))
            points = cv2.findNonZero(foreground)
            if points is not None:
                fx, fy, fw, fh = cv2.boundingRect(points)
                foreground = foreground[fy:fy+fh, fx:fx+fw]
            thumb = cv2.resize(foreground, (33, 9), interpolation=cv2.INTER_AREA)
            bits = thumb[:, 1:] > thumb[:, :-1]
            fingerprint = int.from_bytes(np.packbits(bits).tobytes(), "big")
            # The victim is right-aligned and remains unchanged when assists
            # appear or the row's left edge is temporarily clipped. Keep its
            # colored glyph mask, never the translucent plate, for tracking.
            identity = None
            if len(groups) >= 2 and len(groups[-1]) >= 5*scale:
                glyph = names[:, int(groups[-1][0]):int(groups[-1][-1])+1]
                points = cv2.findNonZero(glyph)
                if points is not None:
                    gx, gy, gw, gh = cv2.boundingRect(points)
                    glyph = glyph[gy:gy+gh, gx:gx+gw]
                    identity = cv2.resize(glyph, (max(1, round(gw*16/gh)), 16), interpolation=cv2.INTER_NEAREST)
            rows.append({
                "attacker": participants[0]["name"] if len(participants) == 2 else None,
                "victim": participants[1]["name"] if len(participants) == 2 else None,
                "bbox": [ox+x, oy+y, w, h], "type": event_type,
                "fingerprint": fingerprint,
                "_identity": identity,
                "attacker_team": self._team(colors, groups[0]) if len(groups) >= 2 else None,
                "victim_team": self._team(colors, groups[-1]) if len(groups) >= 2 else None,
            })
        self.diagnostics["detections"] = len(rows)
        return sorted(rows, key=lambda row: row["bbox"][1])

    def _team(self, colors, columns):
        import numpy as np
        cv2 = self.cv2
        crop = colors[:, int(columns[0]):int(columns[-1])+1]
        text = self._name_mask(crop)
        t = np.count_nonzero(cv2.inRange(crop, (15, 45, 60), (45, 255, 255)) & text)
        ct = np.count_nonzero(cv2.inRange(crop, (90, 45, 60), (125, 255, 255)) & text)
        if max(t, ct) < 5 or max(t, ct) < 2*min(t, ct):
            return None
        return "T" if t > ct else "CT"

    def _name_mask(self, colors):
        import numpy as np
        cv2 = self.cv2
        tint = cv2.inRange(colors, (15, 45, 60), (45, 255, 255)) | cv2.inRange(colors, (90, 45, 60), (125, 255, 255))
        brightness = colors[:, :, 2].astype(np.float32)
        contrast = brightness-np.median(brightness, axis=0, keepdims=True)
        return tint & ((contrast > 30).astype(np.uint8)*255)


class KillFeedTracker:
    """Confirm rows and suppress repeats while they remain visible.

    Victim glyphs survive changing scenery, outlines and assist text. Tracks
    expire after a dropout grace period; simultaneous rows match one-to-one.
    A repeated victim during that grace period (e.g. deathmatch) can be missed.
    """

    def __init__(self, confirmation_frames=2, grace_ms=12000):
        self.confirmation_frames = confirmation_frames
        self.grace_ms = grace_ms
        self.tracks = []
        self.next_id = 1
        self.start_frame = None

    @staticmethod
    def similarity(left, right):
        import cv2
        import numpy as np
        if left is None or right is None:
            return None
        if min(left.shape[1], right.shape[1]) / max(left.shape[1], right.shape[1]) < 0.8:
            return 0.0
        width = max(left.shape[1], right.shape[1])
        a = cv2.resize(left, (width, 16), interpolation=cv2.INTER_NEAREST)
        b = cv2.resize(right, (width, 16), interpolation=cv2.INTER_NEAREST)
        a = (a > 0).astype(np.float32)
        b = (b > 0).astype(np.float32)
        padded = cv2.copyMakeBorder(a, 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=0)
        return float(cv2.matchTemplate(padded, b, cv2.TM_CCORR_NORMED).max())

    def update(self, rows, frame_id, timestamp_ms, source):
        if self.start_frame is None:
            self.start_frame = frame_id
        self.tracks = [t for t in self.tracks if timestamp_ms-t['last'] <= self.grace_ms]
        used = set()
        events = []
        for row in rows:
            best, best_score = None, -1
            for track in self.tracks:
                if track['id'] in used:
                    continue
                previous = track['row']
                if {previous['type'], row['type']} == {'player.kill', 'player.death'}:
                    continue
                a, b = previous.get('victim_team'), row.get('victim_team')
                if a and b and a != b:
                    continue
                score = self.similarity(track.get('identity'), row.get('_identity'))
                if score is None:
                    score = 1.0 if (previous['fingerprint'] ^ row['fingerprint']).bit_count() <= 12 else 0
                if score >= 0.72 and score > best_score:
                    best, best_score = track, score
            if best is None:
                best = {'id': self.next_id, 'row': row, 'identity': row.get('_identity'),
                        'first': timestamp_ms, 'first_frame': frame_id, 'count': 0,
                        'last': timestamp_ms, 'emitted': None}
                self.next_id += 1
                self.tracks.append(best)
            used.add(best['id'])
            best['last'] = timestamp_ms
            best['count'] += 1
            if row['type'] != 'killfeed.kill' or best['row']['type'] == 'killfeed.kill':
                best['row'] = row
            if best.get('identity') is None:
                best['identity'] = row.get('_identity')
            selected = best['row']
            count = best['count']
            if count < self.confirmation_frames:
                continue
            # Give the local outline time to appear before committing a
            # neutral row. Synthetic/unit rows without masks retain 2-frame confirmation.
            if selected['type'] == 'killfeed.kill' and best.get('identity') is not None and timestamp_ms-best['first'] < 1000:
                continue
            if best['emitted'] == selected['type']:
                continue
            correction = best['emitted'] is not None
            best['emitted'] = selected['type']
            row = selected
            events.append({
                "schema_version": "1.0", "type": "killfeed.updated" if correction else row["type"],
                "event_id": f"killfeed-{best['id']}", "classification": row['type'],
                "present_at_start": best['first_frame'] == self.start_frame,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "timestamp_ms": round(timestamp_ms, 3), "frame_id": frame_id,
                "first_observed_ms": round(best['first'], 3), "first_frame_id": best['first_frame'],
                "source": source, "attacker_ocr": row["attacker"], "victim_ocr": row["victim"],
                "local_role": {"player.kill": "attacker", "player.death": "victim"}.get(row["type"]),
                "attacker_team": row.get("attacker_team"), "victim_team": row.get("victim_team"),
                "evidence": {"method": "killfeed_visual_heuristic", "pattern": {"player.kill": "red_outline", "player.death": "red_fill_and_death_view", "killfeed.kill": "neutral_notification"}[row["type"]], "bbox": row["bbox"], "consecutive_readings": count, "names_method": "ocr_unverified" if row["attacker"] or row["victim"] else "not_available"},
            })
        # Unconfirmed tracks require consecutive frames. Emitted tracks survive
        # brief dropouts and preserve their strongest local classification.
        self.tracks = [t for t in self.tracks if t['id'] in used or t['emitted'] is not None]
        return events
