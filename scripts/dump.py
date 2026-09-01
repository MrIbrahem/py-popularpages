# python3 -c "
import bz2
from collections import defaultdict

i_count = 0

lens = defaultdict(int)

with bz2.open("pageviews-202607-user.bz2", "rt") as f:
    for line in f:
        if "ar.wikipedia" not in line:
            continue

        i_count += 1

        if i_count >= 1000:
            break

        print(line, end="")
        len_line = len(line.split(" "))
        lens[len_line] += 1

for k, v in lens.items():
    print(f"lines with {k} columns: {v:,}")
# "
