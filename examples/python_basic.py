"""Legacy call-repetition example; this does not measure agent progress."""
from microloop import Microloop

engine = Microloop("max_repeats: 3")
for step in range(1, 5):
    print(step, engine.verify("read_file", '{"path":"a.txt"}'))
