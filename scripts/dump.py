import bz2

with bz2.open('pageviews-202607-user.bz2', 'rt') as f:
    for i, line in enumerate(f):
        if i >= 1000:
            break
        print(line, end='')
