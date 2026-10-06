These crops come from the user-provided 1280x960, approximately 60 FPS CS2
recording, processed locally at 5 FPS. No nicknames are configured in the detector.

`feed-sequence.npz` contains BGR crops with the original pixels from x=900 to
1279 and y=57 to 299. Keys are sample indices (timestamp = key * 200 ms).
The sparse sequence covers five different local eliminations, assist text,
fading outlines, movement and changing translucent backgrounds. Reconstruct
on a 1280x960 black canvas at (900,57); it does not reconstruct death-view context.

`hud-<sample>.png` contains x=603..677, y=0..89. CT score is 3 throughout;
T score is 1 except sample 275, where it is 2. Samples 0, 15 and 75 show a clock;
the later samples show the planted-C4 icon. The production C4 template was
calibrated separately from the 28.58-second frame.

Full-video validation also matters: these sparse fixtures do not measure exact
detection latency or prove performance on unseen HUD layouts or recordings.
