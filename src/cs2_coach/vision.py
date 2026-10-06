import re


def validate_config(config):
    count = config.get("confirmation_frames", 3)
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError("confirmation_frames must be a positive integer")
    regions = config.get("regions", [])
    if not regions:
        raise ValueError("At least one HUD region is required")
    names = set()
    for region in regions:
        name = region["name"]
        if not name or name in names:
            raise ValueError("Region names must be nonempty and unique")
        names.add(name)
        x, y, w, h = region["rect"]
        if not (0 <= x < 1 and 0 <= y < 1 and w > 0 and h > 0 and x+w <= 1 and y+h <= 1):
            raise ValueError(f"Invalid normalized rectangle: {name}")
        re.compile(region["pattern"])
    return config


def parse_reading(text, region):
    text = re.sub(r"\s+", "", text)
    if not re.fullmatch(region["pattern"], text):
        return None
    value = int(text) if text.isdigit() else text
    if isinstance(value, int):
        if value < region.get("minimum", value) or value > region.get("maximum", value):
            return None
    return value


class HudReader:
    def __init__(self, regions, tesseract_cmd=None):
        import cv2
        import pytesseract
        self.cv2 = cv2
        self.ocr = pytesseract
        if tesseract_cmd:
            self.ocr.pytesseract.tesseract_cmd = tesseract_cmd
        self.ocr.get_tesseract_version()
        self.regions = regions

    def read(self, frame):
        cv2 = self.cv2
        height, width = frame.shape[:2]
        readings = {}
        for region in self.regions:
            x, y, w, h = region["rect"]
            crop = frame[int(y*height):int((y+h)*height), int(x*width):int((x+w)*width)]
            if crop.size == 0:
                readings[region["name"]] = None
                continue
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            original_gray = gray
            scale = region.get("scale", 3)
            if region.get('foreground') == 'bright_text':
                # The HUD plate is translucent: Otsu can classify the scene as
                # part of a digit. Use the bright HUD foreground instead.
                hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
                binary = cv2.inRange(hsv, (0, 0, max(130, round(float(hsv[:, :, 2].max())*.78))), (179, 255, 255))
                binary = cv2.resize(binary, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
            else:
                gray = cv2.resize(gray, None, fx=scale, fy=scale)
                _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            padding = region.get("padding", 0)
            if padding:
                binary = cv2.copyMakeBorder(255-binary, padding, padding, padding, padding, cv2.BORDER_CONSTANT, value=255)
            text = self.ocr.image_to_string(binary, config="--psm 7 -c tessedit_char_whitelist=0123456789:")
            value = parse_reading(text, region)
            if value is None and region.get('foreground') == 'bright_text':
                # Compression can dim the antialiased stroke. Retry a lower
                # foreground threshold only when the first OCR rejects it.
                alternate = cv2.inRange(hsv, (0, 0, max(130, round(float(hsv[:, :, 2].max())*.70))), (179, 255, 255))
                alternate = cv2.resize(alternate, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
                alternate = cv2.copyMakeBorder(255-alternate, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
                value = parse_reading(self.ocr.image_to_string(alternate, config="--psm 7 -c tessedit_char_whitelist=0123456789:"), region)
            if value is None and region.get("zero_fallback"):
                # Small hollow zeros are often discarded by Tesseract. Accept
                # only a single glyph with a tall, centered enclosed hole.
                _, mask = cv2.threshold(original_gray, float(original_gray.max())*0.75, 255, cv2.THRESH_BINARY)
                contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
                if hierarchy is not None:
                    outer = [i for i, item in enumerate(hierarchy[0]) if item[3] < 0 and cv2.contourArea(contours[i]) >= 5]
                    if len(outer) == 1:
                        parent = outer[0]
                        child = hierarchy[0][parent][2]
                        if child >= 0:
                            x, y, w, h = cv2.boundingRect(contours[parent])
                            hx, hy, hw, hh = cv2.boundingRect(contours[child])
                            if hh >= h*0.55 and abs((hy+hh/2)-(y+h/2)) < h*0.15 and hierarchy[0][child][0] < 0:
                                value = 0
            readings[region["name"]] = value
        return readings
