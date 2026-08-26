Put seismic volumes here as .npy files.

A volume must be a 3D NumPy array indexed (iline, xline, time):
the first axis is the inline (x) direction, the second the crossline (y),
and the third is time.

No volume is shipped with this package. run.bat generates a synthetic test
cube on first start; you can also build one yourself at any time:

    .venv\Scripts\python.exe tools\make_synthetic.py

That writes data\seismic_synthetic.npy - 180 x 160 x 320 samples at 4 ms,
containing dipping and folded reflectors, a normal fault and a channel.